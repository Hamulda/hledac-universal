//! SHA-256 hardware acceleration — sha2 crate with Apple Silicon ASM support.
//!
//! On Apple Silicon (aarch64), the `sha2` crate uses ARM NEON crypto instructions
//! (sha256g, sha256h) via `cc-cortex-aes` + `cpuid-bit` detection, giving ~3× speedup
//! over a pure-Scalar implementation at no additional dependency cost.
//!
//! Note: CommonCrypto (CC_SHA256) was removed in macOS 26+. The sha2 crate's ASM path
//! is hardware-accelerated and available on all Apple Silicon chips (M1/M2/M3/M4).

use pyo3::prelude::*;
use rayon::prelude::*;

/// Compute SHA-256 using the sha2 crate (ARM NEON ASM on Apple Silicon).
/// Returns 32-byte digest as Vec<u8>.
pub fn sha256_hw(data: &[u8]) -> Vec<u8> {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    hasher.finalize().to_vec()
}

/// Compute SHA-256 and return as hex string (64 chars).
/// Not registered to Python — internal helper for batch_sha256_hw.
pub fn sha256_hw_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// Batch compute SHA-256 for many items using rayon parallel.
/// Uses cpu_pool() for large batches (>= 128 items).
#[pyfunction]
pub fn batch_sha256_hw(items: Vec<String>) -> Vec<String> {
    let n = items.len();
    if n < 128 {
        items.iter().map(|s| sha256_hw_hex(s.as_bytes())).collect()
    } else {
        crate::cpu_pool().install(|| {
            items
                .par_iter()
                .map(|s| sha256_hw_hex(s.as_bytes()))
                .collect()
        })
    }
}

use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use pbkdf2::pbkdf2_hmac_array;
use rand::RngCore;

/// Derive AES-256 key from password and salt using PBKDF2-HMAC-SHA256.
/// Returns 32-byte key. Iterations: 600,000 (OWASP 2025 recommendation).
fn derive_key(password: &str, salt: &[u8]) -> [u8; 32] {
    pbkdf2_hmac_array::<sha2::Sha256, 32>(password.as_bytes(), salt, 600_000)
}

/// Encrypt a single plaintext with AES-GCM-256.
/// Returns: nonce (12 bytes) || tag (16 bytes) || ciphertext
fn encrypt_aes_gcm_single(key: &[u8; 32], plaintext: &[u8]) -> Vec<u8> {
    let cipher = Aes256Gcm::new_from_slice(key).expect("valid AES-256 key");
    let mut nonce_bytes = [0u8; 12];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ciphertext = cipher.encrypt(nonce, plaintext).expect("encryption failed");
    // Format: nonce || tag || ciphertext (aead::Error already includes tag in ciphertext)
    let mut result = Vec::with_capacity(12 + ciphertext.len());
    result.extend_from_slice(&nonce_bytes);
    result.extend_from_slice(&ciphertext);
    result
}

/// Decrypt a single ciphertext with AES-GCM-256.
/// Input: nonce (12 bytes) || tag (16 bytes) || ciphertext
fn decrypt_aes_gcm_single(key: &[u8; 32], encrypted: &[u8]) -> Result<Vec<u8>, String> {
    if encrypted.len() < 12 + 16 {
        return Err("Encrypted data too short".to_string());
    }
    let cipher = Aes256Gcm::new_from_slice(key).map_err(|e| e.to_string())?;
    let nonce = Nonce::from_slice(&encrypted[..12]);
    let ciphertext = &encrypted[12..];
    cipher.decrypt(nonce, ciphertext).map_err(|e| e.to_string())
}

/// Batch encrypt multiple plaintexts with AES-GCM-256.
///
/// Args:
///     password: Encryption password
///     salt: 16-byte salt (if shorter, zeros are prepended; if longer, truncated)
///     items: List of plaintext bytes (as JSON-encoded strings)
///
/// Returns:
///     List of encrypted blobs: nonce (12) || tag (16) || ciphertext
///     Empty input returns empty list.
///
/// M1 8GB: rayon parallel for n >= 32, serial otherwise.
#[pyfunction]
pub fn batch_encrypt_aes_gcm(password: String, salt: Vec<u8>, items: Vec<String>) -> Vec<Vec<u8>> {
    if items.is_empty() {
        return vec![];
    }
    // Ensure 16-byte salt (prepend zeros if needed, truncate if longer)
    let mut salt16 = [0u8; 16];
    let copy_len = salt.len().min(16);
    salt16[16 - copy_len..].copy_from_slice(&salt[salt.len() - copy_len..]);

    let key = derive_key(&password, &salt16);
    let n = items.len();
    if n < 32 {
        items
            .iter()
            .map(|item| encrypt_aes_gcm_single(&key, item.as_bytes()))
            .collect()
    } else {
        crate::cpu_pool().install(|| {
            items
                .par_iter()
                .map(|item| encrypt_aes_gcm_single(&key, item.as_bytes()))
                .collect()
        })
    }
}

/// Batch decrypt multiple ciphertexts with AES-GCM-256.
///
/// Args:
///     password: Decryption password
///     salt: 16-byte salt (same processing as encrypt)
///     items: List of encrypted blobs
///
/// Returns:
///     List of decrypted plaintext strings on success, None on decryption failure.
///     Item-level error handling — one bad item doesn't fail the batch.
///
/// M1 8GB: rayon parallel for n >= 32, serial otherwise.
#[pyfunction]
pub fn batch_decrypt_aes_gcm(
    password: String,
    salt: Vec<u8>,
    items: Vec<Vec<u8>>,
) -> Vec<Option<String>> {
    if items.is_empty() {
        return vec![];
    }
    // Ensure 16-byte salt (prepend zeros if needed, truncate if longer)
    let mut salt16 = [0u8; 16];
    let copy_len = salt.len().min(16);
    salt16[16 - copy_len..].copy_from_slice(&salt[salt.len() - copy_len..]);

    let key = derive_key(&password, &salt16);
    let n = items.len();
    if n < 32 {
        items
            .iter()
            .map(|encrypted| match decrypt_aes_gcm_single(&key, encrypted) {
                Ok(plaintext) => String::from_utf8(plaintext).ok(),
                Err(_) => None,
            })
            .collect()
    } else {
        crate::cpu_pool().install(|| {
            items
                .par_iter()
                .map(|encrypted| match decrypt_aes_gcm_single(&key, encrypted) {
                    Ok(plaintext) => String::from_utf8(plaintext).ok(),
                    Err(_) => None,
                })
                .collect()
        })
    }
}

/// Register crypto_accelerate functions into the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_sha256_hw))?;
    m.add_function(wrap_pyfunction!(batch_encrypt_aes_gcm))?;
    m.add_function(wrap_pyfunction!(batch_decrypt_aes_gcm))?;
    m.add_function(wrap_pyfunction!(encrypt_aes_gcm_raw))?;
    m.add_function(wrap_pyfunction!(decrypt_aes_gcm_raw))?;
    Ok(())
}

/// Encrypt single plaintext with pre-derived AES-256 key.
/// Input key: 32 bytes (AES-256)
/// Input plaintext: raw bytes
/// Output: nonce(12) || ciphertext || tag(16) — same format as Python cryptography lib
#[pyfunction]
pub fn encrypt_aes_gcm_raw(key: Vec<u8>, plaintext: String) -> Vec<u8> {
    let key: [u8; 32] = match key.try_into() {
        Ok(k) => k,
        Err(_) => return vec![],
    };
    
    let cipher = Aes256Gcm::new_from_slice(&key).expect("valid AES-256 key");
    let mut nonce_bytes = [0u8; 12];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    
    // aes-gcm encrypt returns ciphertext || tag (tag appended)
    let ciphertext_with_tag = match cipher.encrypt(nonce, plaintext.as_bytes()) {
        Ok(ct) => ct,
        Err(_) => return vec![],
    };
    
    // Format: nonce || ciphertext || tag
    let mut result = Vec::with_capacity(12 + ciphertext_with_tag.len());
    result.extend_from_slice(&nonce_bytes);
    result.extend_from_slice(&ciphertext_with_tag);
    result
}

/// Decrypt single ciphertext with pre-derived AES-256 key.
/// Input key: 32 bytes (AES-256)
/// Input encrypted: nonce(12) || ciphertext || tag(16)
/// Output: plaintext as String, or None on failure
#[pyfunction]
pub fn decrypt_aes_gcm_raw(key: Vec<u8>, encrypted: Vec<u8>) -> Option<String> {
    let key: [u8; 32] = match key.try_into() {
        Ok(k) => k,
        Err(_) => return None,
    };
    
    if encrypted.len() < 12 + 16 {
        return None;
    }
    
    let cipher = Aes256Gcm::new_from_slice(&key).ok()?;
    let nonce = Nonce::from_slice(&encrypted[..12]);
    let ciphertext_with_tag = &encrypted[12..];
    
    // aes-gcm decrypt expects ciphertext || tag
    let plaintext = cipher.decrypt(nonce, ciphertext_with_tag).ok()?;
    
    String::from_utf8(plaintext).ok()
}

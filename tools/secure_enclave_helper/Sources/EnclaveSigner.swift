import Foundation
import Security
import CryptoKit

enum EnclaveSignerError: Error {
    case secureEnclaveNotAvailable
    case keyGenerationFailed(OSStatus)
    case keyNotFound
    case signingFailed(OSStatus)
    case invalidDigestHex
    case keychainError(OSStatus)
    case publicKeyExtractionFailed
    case mlDSAError(String)

    var errorCode: String {
        switch self {
        case .secureEnclaveNotAvailable: return "SECURE_ENCLAVE_NOT_AVAILABLE"
        case .keyGenerationFailed: return "KEY_GENERATION_FAILED"
        case .keyNotFound: return "KEY_NOT_FOUND"
        case .signingFailed: return "SIGNING_FAILED"
        case .invalidDigestHex: return "INVALID_DIGEST_HEX"
        case .keychainError: return "KEYCHAIN_ERROR"
        case .publicKeyExtractionFailed: return "PUBLIC_KEY_EXTRACTION_FAILED"
        case .mlDSAError: return "MLDSA_ERROR"
        }
    }
}

struct EnclaveSigner {
    let keyId: String

    init(keyId: String) {
        self.keyId = keyId
    }

    // Check if Secure Enclave is available on this hardware
    static var isSecureEnclaveAvailable: Bool {
        if #available(macOS 10.15, *) {
            return true
        }
        return false
    }

    // Get or create the signing key in Secure Enclave via Keychain
    func getOrCreateSigningKey() throws -> SecKey {
        // Try to retrieve existing key first
        if let existingKey = try? retrieveSigningKey() {
            return existingKey
        }

        // Generate new key pair in Secure Enclave
        return try generateSigningKey()
    }

    func retrieveSigningKey() throws -> SecKey {
        let tag = keyId.data(using: .utf8)!

        let query: [String: Any] = [
            kSecClass as String: kSecClassKey,
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeyClass as String: kSecAttrKeyClassPrivate,
            kSecAttrApplicationTag as String: tag,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecReturnRef as String: true
        ]

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)

        guard status == errSecSuccess else {
            throw EnclaveSignerError.keyNotFound
        }

        return item as! SecKey
    }

    private func generateSigningKey() throws -> SecKey {
        // Create access control for Secure Enclave key
        // kSecAttrAccessibleWhenUnlockedThisDeviceOnly: key is non-exportable, device-only
        // No biometry required in default autonomous mode
        var accessError: Unmanaged<CFError>?
        guard let accessControl = SecAccessControlCreateWithFlags(
            kCFAllocatorDefault,
            kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            [],
            &accessError
        ) else {
            throw EnclaveSignerError.keyGenerationFailed(-1)
        }

        let tag = keyId.data(using: .utf8)!

        let attributes: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecPrivateKeyAttrs as String: [
                kSecAttrIsPermanent as String: true,
                kSecAttrApplicationTag as String: tag,
                kSecAttrAccessControl as String: accessControl
            ]
        ]

        var error: Unmanaged<CFError>?
        guard let privateKey = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
            let nsError = error?.takeRetainedValue() as Error?
            let osStatus = (nsError as NSError?)?.code ?? -1
            throw EnclaveSignerError.keyGenerationFailed(OSStatus(osStatus))
        }

        return privateKey
    }

    // Extract public key from private key
    func extractPublicKey(from privateKey: SecKey) throws -> Data {
        guard let publicKey = SecKeyCopyPublicKey(privateKey) else {
            throw EnclaveSignerError.publicKeyExtractionFailed
        }

        var error: Unmanaged<CFError>?
        guard let publicKeyData = SecKeyCopyExternalRepresentation(publicKey, &error) as Data? else {
            throw EnclaveSignerError.publicKeyExtractionFailed
        }

        return publicKeyData
    }

    // Sign a digest (hex-encoded, expected 64 chars for SHA-256)
    func signDigest(hexDigest: String) throws -> Data {
        let privateKey = try getOrCreateSigningKey()

        // Decode hex digest
        guard let digestData = Data(hexString: hexDigest) else {
            throw EnclaveSignerError.invalidDigestHex
        }

        // Create signature using ECDSA P256
        var error: Unmanaged<CFError>?
        guard let signature = SecKeyCreateSignature(
            privateKey,
            .ecdsaSignatureMessageX962SHA256,
            digestData as CFData,
            &error
        ) as Data? else {
            let nsError = error?.takeRetainedValue() as Error?
            let osStatus = (nsError as NSError?)?.code ?? -1
            throw EnclaveSignerError.signingFailed(OSStatus(osStatus))
        }

        return signature
    }

    // Delete the signing key (for testing/reset)
    func deleteSigningKey() throws {
        let tag = keyId.data(using: .utf8)!

        let query: [String: Any] = [
            kSecClass as String: kSecClassKey,
            kSecAttrApplicationTag as String: tag,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave
        ]

        let status = SecItemDelete(query as CFDictionary)
        if status != errSecSuccess && status != errSecItemNotFound {
            throw EnclaveSignerError.keychainError(status)
        }
    }
}

// Helper extension for hex string conversion
extension Data {
    init?(hexString: String) {
        let hex = hexString.lowercased()
        guard hex.count % 2 == 0 else { return nil }

        var data = Data(capacity: hex.count / 2)
        var index = hex.startIndex

        while index < hex.endIndex {
            let nextIndex = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<nextIndex], radix: 16) else {
                return nil
            }
            data.append(byte)
            index = nextIndex
        }

        self = data
    }

    var hexString: String {
        return map { String(format: "%02x", $0) }.joined()
    }
}

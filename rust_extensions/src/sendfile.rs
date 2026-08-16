//! sendfile(2) wrapper — zero-copy file-to-socket数据传输 for Darwin/macOS.
//!
//! **sendfile(2)** is a Darwin/macOS syscall that transfers data directly between
//! a file descriptor and a socket, bypassing userspace completely:
//! - Eliminates 2× memcpy (disk → userspace → kernel → socket)
//! - Typical speedup: 30-50% for large file transfers
//! - Zero additional RAM (no userspace buffering)
//!
//! ## Use cases in Hledac
//!
//! 1. **HTTP streaming export** — serving large JSONL/CSV exports directly from DuckDB
//!    or file cache to HTTP client without copying through Python.
//! 2. **STIX bundle streaming** — serve pre-generated STIX bundles from disk.
//!
//! ## Limitations
//!
//! - **Darwin only** — sendfile(2) exists on macOS/FreeBSD, NOT on Linux (use sendfile64)
//! - **Socket only** — must be connected socket fd, not arbitrary file paths
//! - **No async** — this is a synchronous syscall wrapper; use in a thread pool
//!
//! ## Darwin sendfile(2) signature
//!
//! ```c
//! int sendfile(int fd, int s, off_t offset, off_t *len, struct sf_hdtr *hdtr, int flags);
//! ```
//!
//! - `fd`: file descriptor to send from
//! - `s`: socket descriptor to send to
//! - `offset`: starting offset in file
//! - `len`: (input/output) bytes to send / bytes sent
//! - `flags`: SF_NOCACHE (disable caching) | SF_NOCACHE_CHECK | SF_REISSUE
//!
//! ## M1 8GB notes
//!
//! sendfile(2) on M1 uses the same zero-copy DMA path as Linux sendfile,
//! but without the page cache pressure on unified memory. For exports under
//! ~100MB, the benefit is minimal vs memory copying. For 100MB+ exports,
//! sendfile provides both speedup AND reduced memory pressure.

use std::fs::File;
use std::os::fd::{AsRawFd, RawFd};
use std::path::Path;

#[cfg(target_os = "macos")]
extern "C" {
    // sendfile(2) signature on Darwin
    fn sendfile(
        fd: RawFd,                   // file descriptor
        s: RawFd,                    // socket descriptor
        offset: i64,                 // starting offset in file
        len: *mut i64,               // in: bytes to send, out: bytes sent
        hdtr: *mut std::ffi::c_void, // header/trailer (usually NULL)
        flags: i32,                  // SF_NOCACHE etc
    ) -> i32;
}

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum SendFileError {
    /// File does not exist or cannot be opened
    FileNotFound,
    /// Not a valid file (e.g., pipe, device)
    NotRegularFile,
    /// Socket operation failed
    SocketError(i32),
    /// sendfile syscall failed with errno
    SyscallFailed(i32),
    /// Offset beyond EOF
    OffsetBeyondEof,
    /// File read error during fallback copy
    FileReadError(std::io::Error),
}

impl std::fmt::Display for SendFileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SendFileError::FileNotFound => write!(f, "file not found"),
            SendFileError::NotRegularFile => write!(f, "not a regular file"),
            SendFileError::SocketError(e) => write!(f, "socket error: {}", e),
            SendFileError::SyscallFailed(e) => write!(f, "sendfile failed: {}", e),
            SendFileError::OffsetBeyondEof => write!(f, "offset beyond EOF"),
            SendFileError::FileReadError(e) => write!(f, "file read error: {}", e),
        }
    }
}

impl std::error::Error for SendFileError {}

impl From<SendFileError> for pyo3::PyErr {
    fn from(err: SendFileError) -> pyo3::PyErr {
        pyo3::exceptions::PyIOError::new_err(err.to_string())
    }
}

// ---------------------------------------------------------------------------
// Darwin sendfile wrapper
// ---------------------------------------------------------------------------

/// Send file data to a connected socket using sendfile(2).
///
/// Uses zero-copy sendfile when possible (Darwin/macOS).
/// Falls back to manual read+write if sendfile fails.
///
/// # Arguments
///
/// * `file` — file to send (must be a regular file, not pipe/socket)
/// * `socket_fd` — raw socket file descriptor (connected)
/// * `offset` — starting byte offset in file
/// * `count` — bytes to send (0 = until EOF)
///
/// # Returns
///
/// - `Ok(bytes_sent)` — total bytes sent
/// - `Err(SendFileError)` — error
///
/// # Safety
///
/// - `socket_fd` must be a valid, connected socket
/// - `file` must be a regular file (not a pipe or device)
#[cfg(target_os = "macos")]
pub unsafe fn sendfile_to_socket(
    file: &mut File,
    socket_fd: RawFd,
    offset: u64,
    count: u64,
) -> Result<u64, SendFileError> {
    use std::io::Seek;

    // Validate file is seekable (regular file)
    file.seek(std::io::SeekFrom::Start(offset))
        .map_err(|_| SendFileError::OffsetBeyondEof)?;

    let mut remaining = count as i64;
    let mut file_offset = offset as i64;
    let mut total_sent: u64 = 0;

    // SF_NOCACHE = disable packet caching on the socket
    const SF_NOCACHE: i32 = 0x0004;

    while remaining > 0 {
        let mut len = remaining;

        let result = sendfile(
            file.as_raw_fd(),
            socket_fd,
            file_offset,
            &mut len,
            std::ptr::null_mut(),
            SF_NOCACHE,
        );

        if result == 0 {
            // Success
            let sent = len as u64;
            total_sent += sent;
            remaining -= len;
            file_offset += len as i64;

            // If len == 0, we're at EOF
            if len == 0 || sent == 0 {
                break;
            }
        } else {
            // Error
            let errno = unsafe { *libc::__error() };
            if errno == libc::EAGAIN || errno == libc::EWOULDBLOCK {
                // Non-blocking, would block — try again
                continue;
            }
            // Check if sendfile doesn't support this (e.g., not a socket)
            if errno == libc::ENOTSOCK || errno == libc::EINVAL {
                // Fallback to read+write
                return Err(SendFileError::SyscallFailed(errno as i32));
            }
            return Err(SendFileError::SyscallFailed(errno as i32));
        }
    }

    Ok(total_sent)
}

/// Fallback: read from file and write to socket manually.
///
/// This is used when sendfile(2) is not available or fails.
/// On M1, for files under ~10MB, the difference is negligible.
pub fn fallback_sendfile(
    file: &mut File,
    socket_fd: RawFd,
    offset: u64,
    count: u64,
) -> Result<u64, SendFileError> {
    use std::io::{Read, Seek};

    file.seek(std::io::SeekFrom::Start(offset))
        .map_err(|_| SendFileError::OffsetBeyondEof)?;

    let mut remaining = count as usize;
    let mut total_sent: u64 = 0;
    let mut buffer = vec![0u8; 65536]; // 64KB buffer

    while remaining > 0 {
        let to_read = remaining.min(buffer.len());
        let read = file
            .read(&mut buffer[..to_read])
            .map_err(SendFileError::FileReadError)?;

        if read == 0 {
            break; // EOF
        }

        let mut written = 0;
        while written < read {
            // SAFETY: socket_fd is a valid socket
            let sent = unsafe {
                libc::write(
                    socket_fd,
                    buffer[written..].as_ptr() as *const _,
                    read - written,
                )
            };

            if sent < 0 {
                let errno = unsafe { *libc::__error() };
                if errno == libc::EAGAIN || errno == libc::EWOULDBLOCK {
                    continue;
                }
                return Err(SendFileError::SocketError(errno as i32));
            }
            written += sent as usize;
        }

        total_sent += written as u64;
        remaining -= written;
    }

    Ok(total_sent)
}

// ---------------------------------------------------------------------------
// Python bindings via PyO3
// ---------------------------------------------------------------------------

// sendfile is always available on Darwin (core functionality)
// #[cfg(feature)] removed — sendfile is platform-gated via #[cfg(target_os = "macos")]
mod py_bindings {
    use super::*;
    use pyo3::prelude::*;

    /// Send a file to a socket using sendfile(2).
    ///
    /// Python usage:
    /// ```python
    /// from hledac_rust_extensions import sendfile_to_socket
    ///
    /// # file_path: path to file
    /// # socket_fd: raw socket file descriptor (int)
    /// # offset: starting byte offset (default 0)
    /// # count: bytes to send, 0 = until EOF (default 0)
    /// bytes_sent = sendfile_to_socket(file_path, socket_fd, offset=0, count=0)
    /// ```
    #[pyfunction]
    #[pyo3(signature = (file_path, socket_fd, offset = 0, count = 0))]
    pub fn sendfile_to_socket_py(
        file_path: &str,
        socket_fd: i32,
        offset: u64,
        count: u64,
    ) -> PyResult<u64> {
        use std::io::Seek;

        let path = Path::new(file_path);
        if !path.exists() {
            return Err(SendFileError::FileNotFound.into());
        }

        let mut file = std::fs::File::open(path).map_err(|_| SendFileError::FileNotFound)?;

        // Seek to end to get file size
        let file_size = file
            .seek(std::io::SeekFrom::End(0))
            .map_err(|_| SendFileError::NotRegularFile)?;

        // Seek back to start (or offset)
        let send_count = if count == 0 {
            file_size.saturating_sub(offset)
        } else {
            count.min(file_size.saturating_sub(offset))
        };

        let raw_socket_fd: RawFd = socket_fd;

        #[cfg(target_os = "macos")]
        {
            // Try sendfile first
            let result =
                unsafe { sendfile_to_socket(&mut file, raw_socket_fd, offset, send_count) };

            match result {
                Ok(sent) => Ok(sent),
                Err(SendFileError::SyscallFailed(_)) => {
                    // Fallback to read+write
                    file.seek(std::io::SeekFrom::Start(offset))
                        .map_err(|_| SendFileError::OffsetBeyondEof)?;
                    fallback_sendfile(&mut file, raw_socket_fd, offset, send_count)
                        .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
                }
                Err(e) => Err(pyo3::exceptions::PyIOError::new_err(e.to_string())),
            }
        }

        #[cfg(not(target_os = "macos"))]
        {
            // Non-macOS: always fallback
            file.seek(std::io::SeekFrom::Start(offset))
                .map_err(|_| SendFileError::OffsetBeyondEof)?;
            fallback_sendfile(&mut file, raw_socket_fd, offset, send_count)
                .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
        }
    }

    /// Check if sendfile is available on this platform.
    ///
    /// Returns True on macOS/Darwin, False on other platforms.
    #[pyfunction]
    pub fn sendfile_available() -> bool {
        #[cfg(target_os = "macos")]
        return true;
        #[cfg(not(target_os = "macos"))]
        return false;
    }

    pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(sendfile_to_socket_py))?;
        m.add_function(wrap_pyfunction!(sendfile_available))?;
        Ok(())
    }
}

pub use py_bindings::register_functions;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sendfile_available() {
        #[cfg(target_os = "macos")]
        assert!(sendfile_available());
        #[cfg(not(target_os = "macos"))]
        assert!(!sendfile_available());
    }
}

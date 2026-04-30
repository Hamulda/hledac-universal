import Foundation

// Helper timeout in seconds - Python adapter should kill process after this
let HELPER_TIMEOUT_SECONDS: TimeInterval = 10.0

// Entry point
let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    printUsage()
    exit(1)
}

let command = arguments[1]

// Set up alarm for timeout
signal(SIGALRM) { _ in
    let result = CommandResult.failure(
        errorCode: "TIMEOUT",
        message: "Helper timed out after \(Int(HELPER_TIMEOUT_SECONDS)) seconds"
    )
    printJSON(result)
    exit(124)
}
alarm(UInt32(HELPER_TIMEOUT_SECONDS))

do {
    let result: CommandResult

    switch command {
    // Existing Secure Enclave commands
    case "status":
        result = Commands.status()

    case "ensure-signing-key":
        result = try parseEnsureSigningKey()

    case "public-key":
        result = try parsePublicKey()

    case "sign-digest":
        result = try parseSignDigest()

    case "delete-key":
        result = try parseDeleteKey()

    // Post-Quantum commands (ML-DSA-65)
    case "pq-status":
        result = Commands.pqStatus()

    case "ensure-mldsa-key":
        result = try parseEnsureMLDSAKey()

    case "mldsa-sign-digest":
        result = try parseMLDSASignDigest()

    case "mldsa-verify":
        result = try parseMLDSAVerify()

    // HPKE Export Encryption commands (X-Wing ML-KEM-768 X25519)
    case "hpke-status":
        result = Commands.hpkeStatus()

    case "hpke-roundtrip":
        result = Commands.hpkeRoundtrip()

    case "hpke-generate-recipient-key":
        result = try parseHPKEGenerateRecipientKey()

    case "hpke-encrypt":
        result = try parseHPKEEncrypt()

    case "hpke-decrypt":
        result = try parseHPKEDecrypt()

    case "--help", "-h", "help":
        printUsage()
        exit(0)

    default:
        result = CommandResult.failure(
            errorCode: "UNKNOWN_COMMAND",
            message: "Unknown command: \(command). Use --help for usage."
        )
    }

    printJSON(result)

} catch {
    let result = CommandResult.failure(
        errorCode: "INTERNAL_ERROR",
        message: error.localizedDescription
    )
    printJSON(result)
    exit(1)
}

// MARK: - Command Parsing

func parseEnsureSigningKey() throws -> CommandResult {
    let args = Array(CommandLine.arguments[2...])
    let keyId = try parseKeyId(args)
    return Commands.ensureSigningKey(keyId: keyId)
}

func parsePublicKey() throws -> CommandResult {
    let args = Array(CommandLine.arguments[2...])
    let keyId = try parseKeyId(args)
    return Commands.publicKey(keyId: keyId)
}

func parseSignDigest() throws -> CommandResult {
    var keyId: String?
    var digestHex: String?

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--key-id":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_KEY_ID",
                    message: "--key-id requires a value"
                )
            }
            keyId = args[i + 1]
            i += 2

        case "--digest-hex":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_DIGEST",
                    message: "--digest-hex requires a 64-char hex string"
                )
            }
            digestHex = args[i + 1]
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let kId = keyId else {
        return CommandResult.failure(
            errorCode: "MISSING_KEY_ID",
            message: "--key-id is required"
        )
    }

    guard let dHex = digestHex else {
        return CommandResult.failure(
            errorCode: "MISSING_DIGEST",
            message: "--digest-hex is required"
        )
    }

    return Commands.signDigest(keyId: kId, digestHex: dHex)
}

func parseDeleteKey() throws -> CommandResult {
    let args = Array(CommandLine.arguments[2...])
    let keyId = try parseKeyId(args)
    return Commands.deleteKey(keyId: keyId)
}

// MARK: - PQ Command Parsing

func parseEnsureMLDSAKey() throws -> CommandResult {
    var keyId: String?
    var level: Int = 65

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--key-id":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_KEY_ID",
                    message: "--key-id requires a value"
                )
            }
            keyId = args[i + 1]
            i += 2

        case "--level":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_LEVEL",
                    message: "--level requires a value"
                )
            }
            level = Int(args[i + 1]) ?? 65
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let kId = keyId else {
        return CommandResult.failure(
            errorCode: "MISSING_KEY_ID",
            message: "--key-id is required"
        )
    }

    return Commands.ensureMLDSAKey(keyId: kId, level: level)
}

func parseMLDSASignDigest() throws -> CommandResult {
    var keyId: String?
    var digestHex: String?
    var level: Int = 65

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--key-id":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_KEY_ID",
                    message: "--key-id requires a value"
                )
            }
            keyId = args[i + 1]
            i += 2

        case "--digest-hex":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_DIGEST",
                    message: "--digest-hex requires a 64-char hex string"
                )
            }
            digestHex = args[i + 1]
            i += 2

        case "--level":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_LEVEL",
                    message: "--level requires a value"
                )
            }
            level = Int(args[i + 1]) ?? 65
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let kId = keyId else {
        return CommandResult.failure(
            errorCode: "MISSING_KEY_ID",
            message: "--key-id is required"
        )
    }

    guard let dHex = digestHex else {
        return CommandResult.failure(
            errorCode: "MISSING_DIGEST",
            message: "--digest-hex is required"
        )
    }

    return Commands.mldsaSignDigest(keyId: kId, digestHex: dHex, level: level)
}

func parseMLDSAVerify() throws -> CommandResult {
    var digestHex: String?
    var signatureHex: String?
    var publicKeyHex: String?
    var level: Int = 65

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--digest-hex":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_DIGEST",
                    message: "--digest-hex requires a value"
                )
            }
            digestHex = args[i + 1]
            i += 2

        case "--signature-hex":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_SIGNATURE",
                    message: "--signature-hex requires a value"
                )
            }
            signatureHex = args[i + 1]
            i += 2

        case "--public-key-hex":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_PUBLIC_KEY",
                    message: "--public-key-hex requires a value"
                )
            }
            publicKeyHex = args[i + 1]
            i += 2

        case "--level":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_LEVEL",
                    message: "--level requires a value"
                )
            }
            level = Int(args[i + 1]) ?? 65
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let dHex = digestHex else {
        return CommandResult.failure(
            errorCode: "MISSING_DIGEST",
            message: "--digest-hex is required"
        )
    }

    guard let sigHex = signatureHex else {
        return CommandResult.failure(
            errorCode: "MISSING_SIGNATURE",
            message: "--signature-hex is required"
        )
    }

    guard let pkHex = publicKeyHex else {
        return CommandResult.failure(
            errorCode: "MISSING_PUBLIC_KEY",
            message: "--public-key-hex is required"
        )
    }

    return Commands.mldsaVerify(digestHex: dHex, signatureHex: sigHex, publicKeyHex: pkHex, level: level)
}

// MARK: - HPKE Command Parsing

func parseHPKEGenerateRecipientKey() throws -> CommandResult {
    var keyId: String?

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--key-id":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_KEY_ID",
                    message: "--key-id requires a value"
                )
            }
            keyId = args[i + 1]
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let kId = keyId else {
        return CommandResult.failure(
            errorCode: "MISSING_KEY_ID",
            message: "--key-id is required"
        )
    }

    return Commands.hpkeGenerateRecipientKey(keyId: kId)
}

func parseHPKEEncrypt() throws -> CommandResult {
    var plaintextB64: String?
    var aadB64: String?
    var recipientKeyB64: String?
    var infoB64: String?

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--plaintext-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_PLAINTEXT",
                    message: "--plaintext-b64 requires a value"
                )
            }
            plaintextB64 = args[i + 1]
            i += 2

        case "--aad-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_AAD",
                    message: "--aad-b64 requires a value"
                )
            }
            aadB64 = args[i + 1]
            i += 2

        case "--recipient-key-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_RECIPIENT_KEY",
                    message: "--recipient-key-b64 requires a value"
                )
            }
            recipientKeyB64 = args[i + 1]
            i += 2

        case "--info-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_INFO",
                    message: "--info-b64 requires a value"
                )
            }
            infoB64 = args[i + 1]
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let pB64 = plaintextB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_PLAINTEXT",
            message: "--plaintext-b64 is required"
        )
    }

    guard let aB64 = aadB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_AAD",
            message: "--aad-b64 is required"
        )
    }

    guard let rB64 = recipientKeyB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_RECIPIENT_KEY",
            message: "--recipient-key-b64 is required"
        )
    }

    return Commands.hpkeEncrypt(plaintextB64: pB64, aadB64: aB64, recipientKeyB64: rB64, infoB64: infoB64)
}

func parseHPKEDecrypt() throws -> CommandResult {
    var encapsulatedKeyB64: String?
    var ciphertextB64: String?
    var aadB64: String?
    var recipientPrivateKeyB64: String?
    var infoB64: String?

    let args = Array(CommandLine.arguments[2...])
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--encapsulated-key-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_ENCAPSULATED_KEY",
                    message: "--encapsulated-key-b64 requires a value"
                )
            }
            encapsulatedKeyB64 = args[i + 1]
            i += 2

        case "--ciphertext-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_CIPHERTEXT",
                    message: "--ciphertext-b64 requires a value"
                )
            }
            ciphertextB64 = args[i + 1]
            i += 2

        case "--aad-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_AAD",
                    message: "--aad-b64 requires a value"
                )
            }
            aadB64 = args[i + 1]
            i += 2

        case "--recipient-private-key-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_RECIPIENT_PRIVATE_KEY",
                    message: "--recipient-private-key-b64 requires a value"
                )
            }
            recipientPrivateKeyB64 = args[i + 1]
            i += 2

        case "--info-b64":
            guard i + 1 < args.count else {
                return CommandResult.failure(
                    errorCode: "MISSING_INFO",
                    message: "--info-b64 requires a value"
                )
            }
            infoB64 = args[i + 1]
            i += 2

        default:
            return CommandResult.failure(
                errorCode: "INVALID_ARGUMENT",
                message: "Unknown argument: \(args[i])"
            )
        }
    }

    guard let ekB64 = encapsulatedKeyB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_ENCAPSULATED_KEY",
            message: "--encapsulated-key-b64 is required"
        )
    }

    guard let ctB64 = ciphertextB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_CIPHERTEXT",
            message: "--ciphertext-b64 is required"
        )
    }

    guard let aB64 = aadB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_AAD",
            message: "--aad-b64 is required"
        )
    }

    guard let rkB64 = recipientPrivateKeyB64 else {
        return CommandResult.failure(
            errorCode: "MISSING_RECIPIENT_PRIVATE_KEY",
            message: "--recipient-private-key-b64 is required"
        )
    }

    return Commands.hpkeDecrypt(
        encapsulatedKeyB64: ekB64,
        ciphertextB64: ctB64,
        aadB64: aB64,
        recipientPrivateKeyB64: rkB64,
        infoB64: infoB64
    )
}

func parseKeyId(_ args: [String]) throws -> String {
    for (i, arg) in args.enumerated() {
        if arg == "--key-id" && i + 1 < args.count {
            return args[i + 1]
        }
    }
    throw CommandError.missingKeyId
}

enum CommandError: Error, LocalizedError {
    case missingKeyId
    case missingDigest

    var errorDescription: String? {
        switch self {
        case .missingKeyId: return "Missing required --key-id argument"
        case .missingDigest: return "Missing required --digest-hex argument"
        }
    }
}

// MARK: - JSON Output

func printJSON(_ result: CommandResult) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]

    do {
        let data = try encoder.encode(result)
        guard let jsonString = String(data: data, encoding: .utf8) else {
            let fallback = #"{"ok":false,"error_code":"JSON_ERROR","message":"Failed to encode response"}"#
            print(fallback)
            return
        }
        print(jsonString)
    } catch {
        let fallback = #"{"ok":false,"error_code":"JSON_ERROR","message":"\#(error.localizedDescription)"}"#
        print(fallback)
    }
}

// MARK: - Usage

func printUsage() {
    print("""
    secure-enclave-helper - Apple Secure Enclave & Post-Quantum signing helper

    USAGE:
      secure-enclave-helper <command> [options]

    SECURE ENCLAVE COMMANDS:
      status                      Check Secure Enclave availability
      ensure-signing-key          Create P-256 signing key if it doesn't exist
      public-key                  Get public key for a key ID
      sign-digest                 Sign a SHA-256 digest with P-256
      delete-key                  Delete a signing key

    POST-QUANTUM COMMANDS (ML-DSA-65, macOS 26+):
      pq-status                   Check ML-DSA-65 availability
      ensure-mldsa-key            Create ML-DSA key if it doesn't exist
      mldsa-sign-digest           Sign a SHA-256 digest with ML-DSA-65
      mldsa-verify                Verify an ML-DSA-65 signature

    HPKE EXPORT ENCRYPTION COMMANDS (X-Wing ML-KEM-768 X25519, macOS 26+):
      hpke-status                 Check HPKE X-Wing availability
      hpke-roundtrip              Test HPKE X-Wing roundtrip (self-contained test)
      hpke-generate-recipient-key  Generate recipient keypair for HPKE
      hpke-encrypt                Encrypt with HPKE X-Wing
      hpke-decrypt                Decrypt with HPKE X-Wing

    OPTIONS:
      --key-id <id>               Key identifier (e.g., com.hledac.sprint.signing.v1)
      --digest-hex <hex>          64-character hex string (SHA-256 digest)
      --signature-hex <hex>       ML-DSA signature bytes (hex)
      --public-key-hex <hex>      ML-DSA public key bytes (hex)
      --level <n>                 Security level (65 for ML-DSA-65)
      --plaintext-b64 <b64>       Base64-encoded plaintext
      --aad-b64 <b64>             Base64-encoded additional authenticated data
      --recipient-key-b64 <b64>   Base64-encoded recipient public key
      --recipient-private-key-b64 <b64>  Base64-encoded recipient private key (for decrypt)
      --encapsulated-key-b64 <b64> Base64-encoded encapsulated key
      --ciphertext-b64 <b64>      Base64-encoded ciphertext
      --info-b64 <b64>            Base64-encoded HPKE info parameter (optional)

    EXAMPLES:
      secure-enclave-helper status
      secure-enclave-helper ensure-signing-key --key-id com.hledac.sprint.signing.v1
      secure-enclave-helper sign-digest --key-id com.hledac.sprint.signing.v1 --digest-hex <64 hex>
      secure-enclave-helper pq-status
      secure-enclave-helper ensure-mldsa-key --key-id com.hledac.pq.signing.v1
      secure-enclave-helper mldsa-sign-digest --key-id com.hledac.pq.signing.v1 --digest-hex <64 hex>
      secure-enclave-helper mldsa-verify --digest-hex <hex> --signature-hex <hex> --public-key-hex <hex>
      secure-enclave-helper hpke-status
      secure-enclave-helper hpke-generate-recipient-key --key-id com.hledac.pq.export.v1
      secure-enclave-helper hpke-encrypt --plaintext-b64 <b64> --aad-b64 <b64> --recipient-key-b64 <b64>
      secure-enclave-helper hpke-decrypt --encapsulated-key-b64 <b64> --ciphertext-b64 <b64> --aad-hash <hex> --recipient-key-b64 <b64>

    EXIT CODES:
      0   Success
      1   General error
      124 Timeout (Python adapter should kill process)

    NOTE: This helper signs digests and encrypts data. It does not process raw OSINT data.
    """)
}
import Foundation
import Security

/// Abstraction for secure storage of the shared household bearer token.
public protocol TokenStoring: Sendable {
    func loadToken() -> String?
    func saveToken(_ token: String) throws
    func deleteToken() throws
}

public enum KeychainStoreError: Error, LocalizedError, Equatable, Sendable {
    case encodingFailed
    case unhandledStatus(OSStatus)

    public var errorDescription: String? {
        switch self {
        case .encodingFailed:
            return "Unable to encode bearer token for Keychain storage."
        case .unhandledStatus(let status):
            return "Keychain operation failed (status \(status))."
        }
    }
}

/// Stores the shared household bearer token in the Apple Keychain.
/// Uses `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` so background push
/// notification actions can read the token after device unlock.
public final class KeychainTokenStore: TokenStoring, Sendable {
    private let service: String
    private let account: String

    public init(
        service: String = "com.example.visionsentinel",
        account: String = "household_bearer_token"
    ) {
        self.service = service
        self.account = account
    }

    public func loadToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    public func saveToken(_ token: String) throws {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            try deleteToken()
            return
        }
        guard let data = trimmed.data(using: .utf8) else {
            throw KeychainStoreError.encodingFailed
        }

        let baseQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]

        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        if updateStatus == errSecItemNotFound {
            var addQuery = baseQuery
            for (k, v) in attributes { addQuery[k] = v }
            let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainStoreError.unhandledStatus(addStatus)
            }
            return
        }
        throw KeychainStoreError.unhandledStatus(updateStatus)
    }

    public func deleteToken() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainStoreError.unhandledStatus(status)
        }
    }
}

/// Thread-safe in-memory token store for unit tests and SwiftUI previews.
public final class InMemoryTokenStore: TokenStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var token: String?

    public init(initialToken: String? = nil) {
        self.token = initialToken
    }

    public func loadToken() -> String? {
        lock.lock()
        defer { lock.unlock() }
        return token
    }

    public func saveToken(_ token: String) throws {
        lock.lock()
        defer { lock.unlock() }
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        self.token = trimmed.isEmpty ? nil : trimmed
    }

    public func deleteToken() throws {
        lock.lock()
        defer { lock.unlock() }
        self.token = nil
    }
}

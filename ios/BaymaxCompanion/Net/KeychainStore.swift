import Foundation
import Security

/// Device credentials live in the iOS Keychain, never in UserDefaults or files.
/// Items are only available after first unlock and never sync to other devices.
enum KeychainStore {
    private static let service = "com.baymax.companion.pairing"

    static func set(_ value: String, for key: String) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: key]
        SecItemDelete(base as CFDictionary)
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    static func get(_ key: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: key,
                                kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(_ key: String) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: key]
        SecItemDelete(q as CFDictionary)
    }
}

/// Persisted pairing (non-secret parts in UserDefaults, the device key in the Keychain).
struct PairedMac: Codable, Equatable {
    var host: String
    var port: Int
    var fingerprint: String
    var deviceId: String

    static let defaultsKey = "baymax.pairedMac"

    static func load() -> PairedMac? {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey) else { return nil }
        return try? JSONDecoder().decode(PairedMac.self, from: data)
    }

    func save() {
        UserDefaults.standard.set(try? JSONEncoder().encode(self), forKey: Self.defaultsKey)
    }

    static func forget() {
        if let m = load() { KeychainStore.delete("key-\(m.deviceId)") }
        UserDefaults.standard.removeObject(forKey: defaultsKey)
    }

    var url: URL? { URL(string: "wss://\(host):\(port)/v1/ws") }
    var deviceKey: String? { KeychainStore.get("key-\(deviceId)") }
}

// Signiert Updates von Benthic (Ed25519 über CryptoKit), damit die App nur Updates installiert, die vom
// Inhaber des privaten Schlüssels stammen. macos/make_release.sh baut und ruft dieses Werkzeug.
//
// Der private Schlüssel liegt nie im Repository, in einem Build oder in einem synchronisierten Ordner:
// entweder im Schlüsselbund der Anmeldung (Eintrag "Benthic release signing", nicht über iCloud geteilt;
// macOS fragt beim Signieren um Erlaubnis) oder in einer Datei mit Rechten 0600 (--key-file=DATEI).
//
//   sign_update keygen [--key-file=DATEI] [--backup=DATEI]
//                                   neuen Schlüssel anlegen (bricht ab, wenn schon einer da ist);
//                                   gibt nur den öffentlichen Schlüssel aus
//   sign_update public [--key-file=DATEI]
//                                   öffentlichen Schlüssel ausgeben (Base64, für BenthicApp.swift)
//   sign_update sign [--key-file=DATEI]
//                                   Nachricht von stdin signieren, Signatur (Base64) nach stdout
//   sign_update verify PUBLIC SIGNATURE
//                                   Nachricht von stdin prüfen; Exit 0 = gültig
//
// Bauen: swiftc -O macos/tools/sign_update.swift -o .swifttmp/sign_update

import CryptoKit
import Foundation
import Security

let service = "Benthic release signing"
let account = "ed25519"

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func option(_ name: String) -> String? {
    CommandLine.arguments.first { $0.hasPrefix("--\(name)=") }.map { String($0.dropFirst(name.count + 3)) }
}

// MARK: Schlüsselbund

func keychainRead() -> Data? {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = item as? Data else { fail("Schlüsselbund: Fehler \(status)") }
    return data
}

func keychainWrite(_ secret: Data) {
    let item: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                               kSecAttrAccount as String: account, kSecValueData as String: secret,
                               kSecAttrSynchronizable as String: false,
                               kSecAttrLabel as String: service,
                               kSecAttrDescription as String: "Ed25519 private key for Benthic updates"]
    let status = SecItemAdd(item as CFDictionary, nil)
    guard status == errSecSuccess else { fail("Schlüsselbund: Eintrag nicht angelegt (Fehler \(status))") }
}

// MARK: Datei

func fileRead(_ path: String) -> Data? {
    let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path) else { return nil }
    if let mode = attrs[.posixPermissions] as? NSNumber, mode.intValue & 0o077 != 0 {
        fail("\(url.path) ist für andere lesbar (Rechte \(String(mode.intValue, radix: 8))); bitte chmod 600")
    }
    guard let text = try? String(contentsOf: url, encoding: .utf8),
          let raw = Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
        fail("\(url.path) enthält keinen Schlüssel in Base64")
    }
    return raw
}

func fileWrite(_ secret: Data, _ path: String) {
    let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    let fm = FileManager.default
    guard !fm.fileExists(atPath: url.path) else { fail("\(url.path) gibt es schon; nichts überschrieben") }
    let folder = url.deletingLastPathComponent()
    do {
        try fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    } catch { fail("Ordner \(folder.path) nicht anlegbar: \(error)") }
    guard fm.createFile(atPath: url.path, contents: Data((secret.base64EncodedString() + "\n").utf8),
                        attributes: [.posixPermissions: 0o600]) else { fail("\(url.path) nicht schreibbar") }
}

func privateKey() -> Curve25519.Signing.PrivateKey {
    let raw: Data?
    if let path = option("key-file") { raw = fileRead(path) } else { raw = keychainRead() }
    guard let raw else { fail("Kein Schlüssel gefunden (erst: sign_update keygen)") }
    guard let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else { fail("Schlüssel unlesbar") }
    return key
}

func stdinData() -> Data { FileHandle.standardInput.readDataToEndOfFile() }

// MARK: Befehle

switch CommandLine.arguments.dropFirst().first ?? "" {
case "keygen":
    let path = option("key-file")
    if let path, FileManager.default.fileExists(atPath: (path as NSString).expandingTildeInPath) {
        fail("\(path) gibt es schon; nichts überschrieben")
    }
    if path == nil, keychainRead() != nil { fail("Im Schlüsselbund liegt schon ein Schlüssel; nichts überschrieben") }
    let key = Curve25519.Signing.PrivateKey()
    if let path { fileWrite(key.rawRepresentation, path) } else { keychainWrite(key.rawRepresentation) }
    if let backup = option("backup") { fileWrite(key.rawRepresentation, backup) }
    print(key.publicKey.rawRepresentation.base64EncodedString())
case "public":
    print(privateKey().publicKey.rawRepresentation.base64EncodedString())
case "sign":
    let message = stdinData()
    guard !message.isEmpty else { fail("Keine Nachricht auf stdin") }
    guard let signature = try? privateKey().signature(for: message) else { fail("Signieren fehlgeschlagen") }
    print(signature.base64EncodedString())
case "verify":
    let args = Array(CommandLine.arguments.dropFirst(2))
    guard args.count == 2, let raw = Data(base64Encoded: args[0]), let sig = Data(base64Encoded: args[1]),
          let pub = try? Curve25519.Signing.PublicKey(rawRepresentation: raw) else { fail("Aufruf: verify PUBLIC SIGNATURE") }
    exit(pub.isValidSignature(sig, for: stdinData()) ? 0 : 1)
default:
    fail("Aufruf: sign_update keygen|public|sign|verify (siehe Kopf von macos/tools/sign_update.swift)")
}

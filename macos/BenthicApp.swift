// Benthic: native macOS-Shell (Quellen prüfen und laden).
//
// Startet das Python-Backend (gebündelt oder aus dem Dev-Projekt) und zeigt
// die Web-Oberfläche in einem nativen WKWebView-Fenster.

import Cocoa
import WebKit
import UserNotifications
import UniformTypeIdentifiers
import SwiftUI
import Translation  // schwach gelinkt (build_app.sh): erst ab macOS 15 vorhanden

/// Port und Datenordner: Standardwerte, eigene Builds setzen sie in der Info.plist
/// (build_app.sh --port / --data-folder), damit mehrere Builds nebeneinander laufen.
let backendPort: UInt16 = {
    let value = Bundle.main.object(forInfoDictionaryKey: "BenthicPort")
    if let n = value as? NSNumber { return n.uint16Value }
    if let s = value as? String, let n = UInt16(s) { return n }
    return 8327
}()
let backendHost = "http://127.0.0.1:\(backendPort)"
let dataFolderName = (Bundle.main.object(forInfoDictionaryKey: "BenthicDataFolder") as? String) ?? "Benthic"
let appDisplayName = (Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String) ?? "Benthic"

/// Sprache der Oberfläche: Englisch (Standard) oder Deutsch. Die Weboberfläche meldet die Wahl
/// beim Laden (benthicLang); Menüs und Dialoge folgen sofort, Texte von macOS selbst (etwa
/// Kontextmenüs) ab dem nächsten Start über AppleLanguages.
let languageKey = "BenthicLanguage"

func L(_ de: String, _ en: String) -> String {
    UserDefaults.standard.string(forKey: languageKey) == "de" ? de : en
}

final class LanguageBridge: NSObject, WKScriptMessageHandler {
    var onChange: (() -> Void)?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let lang = (message.body as? [String: Any])?["lang"] as? String, ["en", "de"].contains(lang) else { return }
        let defaults = UserDefaults.standard
        guard defaults.string(forKey: languageKey) != lang else { return }
        defaults.set(lang, forKey: languageKey)
        defaults.set([lang], forKey: "AppleLanguages")
        onChange?()
    }
}

/// Hintergrund aus den Design-Tokens (--bg), passend zu Hell- und Dunkelmodus.
let benthicBackground = NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(srgbRed: 4 / 255, green: 10 / 255, blue: 23 / 255, alpha: 1)
        : NSColor(srgbRed: 246 / 255, green: 248 / 255, blue: 252 / 255, alpha: 1)
}

/// Leitet Benachrichtigungen aus der Web-Oberfläche an macOS weiter.
final class NotificationBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        let title = (body["title"] as? String) ?? "Benthic"
        let text = (body["body"] as? String) ?? ""
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = text
        content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
    }
}

/// Native Auswahl von Ordnern und Dateien für die Weboberfläche (Ersteinrichtung).
/// Die Seite ruft window.webkit.messageHandlers.benthicPick.postMessage({id, kind, types, start})
/// und bekommt den Pfad über window.benthicPicked({id, path}) zurück.
final class PickerBridge: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let id = body["id"] as? String else { return }
        let wantsFolder = (body["kind"] as? String ?? "folder") == "folder"
        let panel = NSOpenPanel()
        panel.canChooseDirectories = wantsFolder
        panel.canChooseFiles = !wantsFolder
        panel.canCreateDirectories = wantsFolder
        panel.allowsMultipleSelection = false
        panel.prompt = L("Auswählen", "Choose")
        if let start = body["start"] as? String, !start.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: (start as NSString).expandingTildeInPath)
        }
        if !wantsFolder, let types = body["types"] as? [String], !types.isEmpty {
            panel.allowedContentTypes = types.compactMap { UTType(filenameExtension: $0) }
        }
        let respond: (String?) -> Void = { [weak self] path in
            let payload: [String: Any] = ["id": id, "path": path ?? NSNull()]
            guard let data = try? JSONSerialization.data(withJSONObject: payload),
                  let json = String(data: data, encoding: .utf8) else { return }
            self?.webView?.evaluateJavaScript("window.benthicPicked && window.benthicPicked(\(json))",
                                              completionHandler: nil)
        }
        if let window = webView?.window {
            panel.beginSheetModal(for: window) { r in respond(r == .OK ? panel.url?.path : nil) }
        } else {
            respond(panel.runModal() == .OK ? panel.url?.path : nil)
        }
    }
}

/// Zahl der offenen Quellen eines laufenden Auftrags als Badge im Dock.
final class DockBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        let badge = ((message.body as? [String: Any])?["badge"] as? String) ?? ""
        NSApp.dockTile.badgeLabel = badge.isEmpty ? nil : badge
    }
}

/// Darstellung aus den Einstellungen: hell, dunkel oder wie das System.
final class AppearanceBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        switch (message.body as? [String: Any])?["theme"] as? String {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: NSApp.appearance = nil
        }
    }
}

/// Übersetzt Sätze auf dem Mac (Apple Translation, ab macOS 15), damit die Suche nach Belegstellen auch
/// trifft, wenn Satz und Quelle verschiedene Sprachen haben. Fehlt ein Sprachpaar, fragt macOS selbst,
/// ob es geladen werden soll. Die Seite ruft
/// window.webkit.messageHandlers.benthicTranslate.postMessage({id, text, source, target}).
final class TranslateBridge: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?
    private var model: AnyObject?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let id = body["id"] as? String,
              let text = body["text"] as? String, let target = body["target"] as? String else { return }
        let source = body["source"] as? String
        guard #available(macOS 15.0, *) else {
            reply(id, nil, "unsupported")
            return
        }
        translator().enqueue(text: text, source: source, target: target) { [weak self] result, error in
            self?.reply(id, result, error)
        }
    }

    @available(macOS 15.0, *)
    private func translator() -> TranslatorModel {
        if let m = model as? TranslatorModel { return m }
        let m = TranslatorModel()
        let hosting = NSHostingView(rootView: TranslatorView(model: m))
        hosting.frame = NSRect(x: 0, y: 0, width: 1, height: 1)
        hosting.alphaValue = 0.01  // unsichtbar, aber im Fenster: sonst startet .translationTask nicht
        webView?.addSubview(hosting)
        model = m
        return m
    }

    private func reply(_ id: String, _ text: String?, _ error: String?) {
        var obj: [String: Any] = ["id": id]
        if let text { obj["text"] = text }
        if let error { obj["error"] = error }
        guard let data = try? JSONSerialization.data(withJSONObject: obj),
              let json = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("window.benthicTranslated && window.benthicTranslated(\(json))",
                                             completionHandler: nil)
        }
    }
}

@available(macOS 15.0, *)
final class TranslatorModel: ObservableObject {
    struct Job {
        let text: String
        let done: (String?, String?) -> Void
    }
    @Published var configuration: TranslationSession.Configuration?
    private var queue: [Job] = []

    func enqueue(text: String, source: String?, target: String, done: @escaping (String?, String?) -> Void) {
        queue.append(Job(text: text, done: done))
        let src = source.map { Locale.Language(identifier: $0) }
        let tgt = Locale.Language(identifier: target)
        if configuration?.source == src && configuration?.target == tgt {
            configuration?.invalidate()  // gleiche Sprachen: Aufgabe erneut auslösen
        } else {
            configuration = TranslationSession.Configuration(source: src, target: tgt)
        }
    }

    @MainActor
    func run(_ session: TranslationSession) async {
        let jobs = queue
        queue.removeAll()
        do {
            try await session.prepareTranslation()  // lädt fehlende Sprachen nach Rückfrage von macOS
        } catch {
            jobs.forEach { $0.done(nil, "unavailable") }
            return
        }
        for job in jobs {
            do {
                let response = try await session.translate(job.text)
                job.done(response.targetText, nil)
            } catch {
                job.done(nil, "failed")
            }
        }
    }
}

@available(macOS 15.0, *)
struct TranslatorView: View {
    @ObservedObject var model: TranslatorModel

    var body: some View {
        Color.clear
            .frame(width: 1, height: 1)
            .translationTask(model.configuration) { session in
                await model.run(session)
            }
    }
}

/// Prüfbericht als PDF: die Seite /report/<id> des Backends, über macOS gedruckt (A4, mit Seitenumbrüchen).
/// Die Seite ruft window.webkit.messageHandlers.benthicReport.postMessage({url, name}).
final class ReportBridge: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    weak var window: NSWindow?
    private var printWindow: NSWindow?
    private var destination: URL?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let raw = body["url"] as? String,
              let url = URL(string: raw), raw.hasPrefix(backendHost + "/report/"), let window else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (body["name"] as? String) ?? "Benthic.pdf"
        panel.allowedContentTypes = [.pdf]
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let dest = panel.url else { return }
            self?.render(url, to: dest)
        }
    }

    private func render(_ url: URL, to dest: URL) {
        // Eigenes, unsichtbares Fenster: WebKit druckt nur Ansichten, die in einem Fenster liegen.
        let frame = NSRect(x: -4000, y: -4000, width: 794, height: 1123)
        let w = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
        let view = WKWebView(frame: NSRect(origin: .zero, size: frame.size))
        view.navigationDelegate = self
        w.contentView = view
        printWindow = w
        destination = dest
        view.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let dest = destination, let host = printWindow else { return }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.paperSize = NSSize(width: 595.28, height: 841.89)
        info.topMargin = 40
        info.bottomMargin = 40
        info.leftMargin = 36
        info.rightMargin = 36
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isVerticallyCentered = false
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = dest
        let op = webView.printOperation(with: info)
        op.showsPrintPanel = false
        op.showsProgressPanel = false
        op.view?.frame = webView.bounds
        op.runModal(for: host, delegate: self, didRun: #selector(printed(_:success:contextInfo:)), contextInfo: nil)
    }

    @objc func printed(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        let dest = destination
        printWindow = nil
        destination = nil
        if success, let dest { NSWorkspace.shared.activateFileViewerSelecting([dest]) }
    }
}

/// Eine Datei im Finder zeigen (Stapelprüfung). Die Seite ruft
/// window.webkit.messageHandlers.benthicReveal.postMessage({path}).
final class RevealBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let path = body["path"] as? String else { return }
        let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

/// Aktualisieren aus der App. Das Backend hat das DMG schon geladen und seine Prüfsumme mit dem
/// Manifest verglichen (updates.py). Hier: DMG einhängen, die App darin prüfen (gleiche Bundle-Kennung,
/// erwartete Version, gültige Signatur), neben die laufende App kopieren und nach dem Beenden von einem
/// kleinen Skript austauschen und neu starten lassen. Die Seite ruft
/// window.webkit.messageHandlers.benthicUpdate.postMessage({id, action: "install"|"open", path, version});
/// die Antwort kommt über window.benthicUpdateDone(id, ok, code).
final class UpdateBridge: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let id = body["id"] as? String,
              let path = body["path"] as? String else { return }
        let dmg = URL(fileURLWithPath: path).standardizedFileURL
        guard let updates = updatesFolder(), dmg.path.hasPrefix(updates.path + "/"), dmg.pathExtension == "dmg",
              FileManager.default.fileExists(atPath: dmg.path) else {
            reply(id, false, "bad-file")
            return
        }
        if body["action"] as? String == "open" {
            NSWorkspace.shared.open(dmg)
            reply(id, true, "opened")
            return
        }
        let version = body["version"] as? String ?? ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let code = self?.install(dmg: dmg, version: version)
            DispatchQueue.main.async {
                self?.reply(id, code == nil, code ?? "started")
                if code == nil {
                    // Kurz warten, damit die Seite "startet neu" zeigen kann; das Skript wartet auf das Ende.
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { NSApp.terminate(nil) }
                }
            }
        }
    }

    private func updatesFolder() -> URL? {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/\(dataFolderName)/updates").standardizedFileURL
    }

    private func reply(_ id: String, _ ok: Bool, _ code: String) {
        let js = "window.benthicUpdateDone && window.benthicUpdateDone(\(jsString(id)), \(ok), \(jsString(code)))"
        webView?.evaluateJavaScript(js, completionHandler: nil)
    }

    private func jsString(_ s: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [s])
        let arr = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }

    @discardableResult
    private func run(_ tool: String, _ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return -1 }
        p.waitUntilExit()
        return p.terminationStatus
    }

    /// nil = Austausch vorbereitet, die App darf sich beenden; sonst ein Fehlercode für die Seite.
    private func install(dmg: URL, version: String) -> String? {
        let fm = FileManager.default
        let current = Bundle.main.bundleURL.resolvingSymlinksInPath()
        // Aus "Downloads" gestartet (App Translocation): kein fester Ort zum Ersetzen. Direkt aus dem DMG
        // gestartet scheitert es unten am schreibgeschützten Ordner.
        if current.path.contains("/AppTranslocation/") { return "location" }
        let parent = current.deletingLastPathComponent()
        guard fm.isWritableFile(atPath: parent.path), fm.isWritableFile(atPath: current.path) else { return "not-writable" }

        let mount = fm.temporaryDirectory.appendingPathComponent("benthic-update-\(UUID().uuidString)")
        guard (try? fm.createDirectory(at: mount, withIntermediateDirectories: true)) != nil else { return "mount" }
        guard run("/usr/bin/hdiutil", ["attach", dmg.path, "-nobrowse", "-readonly", "-noautoopen",
                                        "-noverify", "-mountpoint", mount.path]) == 0 else { return "mount" }
        defer {
            run("/usr/bin/hdiutil", ["detach", mount.path, "-force"])
            try? fm.removeItem(at: mount)
        }

        let apps = (try? fm.contentsOfDirectory(at: mount, includingPropertiesForKeys: nil)) ?? []
        guard let app = apps.first(where: { url in
            guard url.pathExtension == "app", let b = Bundle(url: url) else { return false }
            return b.bundleIdentifier == Bundle.main.bundleIdentifier
                && (b.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) == version
        }) else { return "wrong-app" }
        guard run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path]) == 0 else { return "signature" }

        let name = current.lastPathComponent
        let staged = parent.appendingPathComponent(".\(name).update-\(version)")
        let old = parent.appendingPathComponent(".\(name).old-\(version)")
        try? fm.removeItem(at: staged)
        try? fm.removeItem(at: old)
        guard run("/usr/bin/ditto", [app.path, staged.path]) == 0 else { return "copy" }
        run("/usr/bin/xattr", ["-dr", "com.apple.quarantine", staged.path])

        // Austausch nach dem Beenden: alte App beiseite, neue an ihren Platz, Icon-Cache auffrischen, starten.
        let script = """
        #!/bin/sh
        pid="$1"; app="$2"; new="$3"; old="$4"
        i=0
        while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 300 ]; do sleep 0.2; i=$((i+1)); done
        if kill -0 "$pid" 2>/dev/null; then rm -rf "$new"; rm -f "$0"; exit 1; fi
        if mv "$app" "$old"; then
          if mv "$new" "$app"; then rm -rf "$old"; else mv "$old" "$app"; fi
        fi
        touch "$app"
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1
        open "$app"
        rm -f "$0"
        """
        let scriptURL = fm.temporaryDirectory.appendingPathComponent("benthic-update-\(UUID().uuidString).sh")
        guard (try? script.write(to: scriptURL, atomically: true, encoding: .utf8)) != nil else { return "copy" }
        let swap = Process()
        swap.executableURL = URL(fileURLWithPath: "/bin/sh")
        swap.arguments = [scriptURL.path, String(ProcessInfo.processInfo.processIdentifier),
                          current.path, staged.path, old.path]
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "BENTHIC_TEST_UPDATE")  // die neue App startet wie gewohnt
        swap.environment = env
        swap.standardOutput = FileHandle.nullDevice
        swap.standardError = FileHandle.nullDevice
        do { try swap.run() } catch { return "copy" }
        return nil
    }
}

/// Dienste-Menü ("Literatur mit Benthic prüfen"): markierter Text aus jeder App kommt hier an.
/// Der Name der Methode folgt NSMessage in der Info.plist (checkReferences:userData:error:).
final class ServiceProvider: NSObject {
    var deliver: ((String, String) -> Void)?

    @objc func checkReferences(_ pboard: NSPasteboard, userData: String?,
                               error: AutoreleasingUnsafeMutablePointer<NSString?>) {
        guard let text = pboard.string(forType: .string), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            error.pointee = L("Kein Text markiert.", "No text was selected.") as NSString
            return
        }
        deliver?(text, "service")
    }
}

/// URL-Schema der App (Standard "benthic", eigene Builds setzen ein eigenes).
let urlScheme: String = {
    let types = Bundle.main.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]]
    return ((types?.first?["CFBundleURLSchemes"] as? [String])?.first) ?? "benthic"
}()

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var hasLoaded = false
    private let notificationBridge = NotificationBridge()
    private let pickerBridge = PickerBridge()
    private let dockBridge = DockBridge()
    private let appearanceBridge = AppearanceBridge()
    private let languageBridge = LanguageBridge()
    private let translateBridge = TranslateBridge()
    private let reportBridge = ReportBridge()
    private let revealBridge = RevealBridge()
    private let updateBridge = UpdateBridge()
    private let serviceProvider = ServiceProvider()
    private var pageReady = false
    private var inbox: [(String, String)] = []

    // MARK: - Lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        buildMenu()
        buildWindow()
        startBackendAndLoad()
        serviceProvider.deliver = { [weak self] text, source in self?.receive(text, source: source) }
        NSApp.servicesProvider = serviceProvider
        NSUpdateDynamicServices()
    }

    // MARK: - Text aus anderen Apps (URL-Schema, Dienste-Menü, Lesezeichen im Browser)

    /// benthic://check?text=… oder benthic://check?clipboard=1 (lange Texte über die Zwischenablage).
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme?.lowercased() == urlScheme {
            let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in items.first(where: { $0.name == name })?.value }
            if let text = value("text"), !text.isEmpty {
                receive(text, source: value("source") ?? "link")
            } else if value("clipboard") != nil, let text = NSPasteboard.general.string(forType: .string) {
                receive(text, source: value("source") ?? "link")
            } else {
                window?.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            }
        }
    }

    private func receive(_ text: String, source: String) {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        inbox.append((String(text.prefix(2_000_000)), source))
        flushInbox()
    }

    private func flushInbox() {
        guard pageReady, let webView = webView else { return }
        let pending = inbox
        inbox.removeAll()
        for (text, source) in pending {
            guard let data = try? JSONSerialization.data(withJSONObject: ["text": text, "source": source]),
                  let json = String(data: data, encoding: .utf8) else { continue }
            webView.evaluateJavaScript("window.benthicReceive && window.benthicReceive(\(json))", completionHandler: nil)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Die Seite meldet sich erst nach dem Laden ihrer Skripte; kurz warten, dann zustellen.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.pageReady = true
            self?.flushInbox()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        backend?.terminate()
        backend = nil
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: - Fenster & Menü

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1120, height: 800)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = appDisplayName
        window.backgroundColor = benthicBackground
        window.minSize = NSSize(width: 760, height: 560)
        window.center()

        let config = WKWebViewConfiguration()
        config.userContentController.add(notificationBridge, name: "benthicNotify")
        config.userContentController.add(pickerBridge, name: "benthicPick")
        config.userContentController.add(dockBridge, name: "benthicDock")
        config.userContentController.add(appearanceBridge, name: "benthicTheme")
        config.userContentController.add(languageBridge, name: "benthicLang")
        config.userContentController.add(translateBridge, name: "benthicTranslate")
        config.userContentController.add(reportBridge, name: "benthicReport")
        config.userContentController.add(revealBridge, name: "benthicReveal")
        config.userContentController.add(updateBridge, name: "benthicUpdate")
        languageBridge.onChange = { [weak self] in self?.buildMenu() }
        // Die Seite erfährt, dass sie in der App läuft und unter welchem URL-Schema (für das Lesezeichen).
        // BENTHIC_TEST_UPDATE=1 (nur für den Test des Aktualisierens): ein gefundenes Update startet von selbst.
        let autoUpdate = ProcessInfo.processInfo.environment["BENTHIC_TEST_UPDATE"] == "1"
        let shell = "window.benthicShell = {scheme: \"\(urlScheme)\", services: true, autoUpdate: \(autoUpdate)};"
        config.userContentController.addUserScript(WKUserScript(source: shell, injectionTime: .atDocumentStart,
                                                                forMainFrameOnly: true))
        webView = WKWebView(frame: rect, configuration: config)
        pickerBridge.webView = webView
        translateBridge.webView = webView
        updateBridge.webView = webView
        reportBridge.window = window
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        // App-Menü
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        let about = NSMenuItem(title: L("Über \(appDisplayName)", "About \(appDisplayName)"),
                               action: #selector(showAbout(_:)), keyEquivalent: "")
        about.target = self
        appMenu.addItem(about)
        let licenses = NSMenuItem(title: L("Lizenzen und Hinweise …", "Licences and Notices…"),
                                  action: #selector(showLicenses(_:)), keyEquivalent: "")
        licenses.target = self
        appMenu.addItem(licenses)
        appMenu.addItem(NSMenuItem.separator())
        let prefs = NSMenuItem(title: L("Einstellungen …", "Settings…"), action: #selector(showSettings(_:)), keyEquivalent: ",")
        prefs.target = self
        appMenu.addItem(prefs)
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: L("\(appDisplayName) ausblenden", "Hide \(appDisplayName)"),
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: L("Andere ausblenden", "Hide Others"),
                                     action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: L("Alle einblenden", "Show All"),
                        action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(
            withTitle: L("\(appDisplayName) beenden", "Quit \(appDisplayName)"),
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        appMenuItem.submenu = appMenu

        // Bearbeiten-Menü (Kopieren/Einfügen im DOI-Feld)
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: L("Bearbeiten", "Edit"))
        editMenu.addItem(withTitle: L("Rückgängig", "Undo"), action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: L("Wiederholen", "Redo"), action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: L("Ausschneiden", "Cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: L("Kopieren", "Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: L("Einsetzen", "Paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: L("Alles auswählen", "Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu

        // Fenster-Menü
        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: L("Fenster", "Window"))
        windowMenu.addItem(withTitle: L("Im Dock ablegen", "Minimize"), action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: L("Zoomen", "Zoom"), action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenuItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = mainMenu
    }

    /// ⌘, öffnet die Einstellungen der Weboberfläche.
    @objc private func showSettings(_ sender: Any?) {
        webView?.evaluateJavaScript("typeof showView === 'function' && showView('settings')", completionHandler: nil)
    }

    /// Über-Fenster mit Deskriptor, Leitsatz, Lizenz und Link zu den Drittanbieter-Hinweisen.
    @objc private func showAbout(_ sender: Any?) {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.paragraphSpacing = 4
        let credits = NSMutableAttributedString(
            string: L("Quellen prüfen und laden", "Check and fetch sources") + "\n",
            attributes: [.font: NSFont.systemFont(ofSize: 12, weight: .semibold),
                         .foregroundColor: NSColor.labelColor, .paragraphStyle: style])
        credits.append(NSAttributedString(
            string: L("Open Access zuerst, sonst der Weg über deinen Hochschulzugang.",
                      "Open access first, otherwise through your university access.") + "\n\n"
                + L("Entwickelt von ", "Developed by "),
            attributes: [.font: NSFont.systemFont(ofSize: 11),
                         .foregroundColor: NSColor.secondaryLabelColor, .paragraphStyle: style]))
        credits.append(NSAttributedString(
            string: "Sinan Isoglu",
            attributes: [.font: NSFont.systemFont(ofSize: 11), .link: URL(string: "https://isoglu.com")!,
                         .paragraphStyle: style]))
        credits.append(NSAttributedString(
            string: "\n" + L("Freie Software unter der GNU AGPL 3.0.", "Free software under the GNU AGPL 3.0.") + "\n",
            attributes: [.font: NSFont.systemFont(ofSize: 11),
                         .foregroundColor: NSColor.secondaryLabelColor, .paragraphStyle: style]))
        if let notices = noticesURL() {
            credits.append(NSAttributedString(
                string: L("Lizenzen und Hinweise", "Licences and notices"),
                attributes: [.font: NSFont.systemFont(ofSize: 11), .link: notices, .paragraphStyle: style]))
        }
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: appDisplayName, .credits: credits])
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Drittanbieter-Hinweise (build_app.sh legt sie ins Bundle, im Dev-Betrieb liegen sie im Projekt).
    private func noticesURL() -> URL? {
        var candidates: [URL] = []
        if let res = Bundle.main.resourceURL {
            candidates.append(res.appendingPathComponent("backend/THIRD_PARTY_NOTICES.txt"))
        }
        if let dev = ProcessInfo.processInfo.environment["BENTHIC_BACKEND_DIR"] {
            candidates.append(URL(fileURLWithPath: dev).appendingPathComponent("THIRD_PARTY_NOTICES.md"))
        }
        return candidates.first { FileManager.default.fileExists(atPath: $0.path) }
    }

    @objc private func showLicenses(_ sender: Any?) {
        guard let url = noticesURL() else { return }
        NSWorkspace.shared.open(url)
    }

    // MARK: - Backend

    /// Sucht das Python-Backend: (1) gebündelt, (2) env BENTHIC_BACKEND_DIR (Entwicklung).
    private func resolveBackend() -> (python: String, script: String, workdir: String)? {
        let fm = FileManager.default

        // 1) Gebündelt in der App (selbstcontainert).
        if let res = Bundle.main.resourceURL {
            let backendDir = res.appendingPathComponent("backend")
            let py = backendDir.appendingPathComponent("runtime/bin/python3.12")
            let runPy = backendDir.appendingPathComponent("run.py")
            if fm.isExecutableFile(atPath: py.path) && fm.fileExists(atPath: runPy.path) {
                return (py.path, runPy.path, backendDir.path)
            }
        }

        // 2) Umgebungsvariable BENTHIC_BACKEND_DIR (Dev-Override).
        if let env = ProcessInfo.processInfo.environment["BENTHIC_BACKEND_DIR"] {
            let dir = URL(fileURLWithPath: env)
            let py = dir.appendingPathComponent(".venv/bin/python")
            let runPy = dir.appendingPathComponent("run.py")
            if fm.isExecutableFile(atPath: py.path) && fm.fileExists(atPath: runPy.path) {
                return (py.path, runPy.path, env)
            }
        }

        return nil
    }

    private func startBackendAndLoad() {
        guard let (python, script, workdir) = resolveBackend() else {
            showError(L("Python-Backend nicht gefunden. Bitte die App neu bauen (macos/build_app.sh) oder BENTHIC_BACKEND_DIR setzen.",
                        "Python backend not found. Rebuild the app (macos/build_app.sh) or set BENTHIC_BACKEND_DIR."))
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [script]
        proc.currentDirectoryURL = URL(fileURLWithPath: workdir)

        var env = ProcessInfo.processInfo.environment

        // Beschreibbare Daten (Einstellungen, Bibliothek, Downloads) in den Benutzer-Ordner.
        let appSupport = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/\(dataFolderName)")
        env["BENTHIC_DATA_DIR"] = appSupport.path
        env["BENTHIC_PORT"] = String(backendPort)

        // Backend beendet sich selbst, sobald diese App verschwindet.
        env["BENTHIC_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)

        // Gebündelte Abhängigkeiten über PYTHONPATH.
        let isBundled = workdir.hasSuffix("/backend")
        if isBundled {
            let sp = URL(fileURLWithPath: workdir).appendingPathComponent("site-packages").path
            let existing = env["PYTHONPATH"] ?? ""
            env["PYTHONPATH"] = existing.isEmpty ? sp : "\(sp):\(existing)"
            // Kein Bytecode ins Bundle schreiben, das bräche die Signatur.
            // build_app.sh liefert die .pyc vorkompiliert mit.
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        }
        proc.environment = env

        // Backend-Log in ~/Library/Logs/<Datenordner>-backend.log
        let logDir = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Logs")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let logPath = logDir.appendingPathComponent("\(dataFolderName)-backend.log")
        FileManager.default.createFile(atPath: logPath.path, contents: nil)
        let logHandle = FileHandle(forWritingAtPath: logPath.path) ?? FileHandle.nullDevice
        proc.standardOutput = logHandle
        proc.standardError = logHandle

        backend = proc
        do {
            try proc.run()
        } catch {
            showError(L("Backend konnte nicht gestartet werden: ", "The backend could not be started: ") + error.localizedDescription)
            return
        }
        waitForBackendAndLoad()
    }

    private func waitForBackendAndLoad() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let healthURL = URL(string: "\(backendHost)/api/health")!
            for _ in 0..<120 {
                if self?.hasLoaded == true { return }
                if let data = try? Data(contentsOf: healthURL), !data.isEmpty {
                    DispatchQueue.main.async {
                        guard let self = self, !self.hasLoaded else { return }
                        self.hasLoaded = true
                        self.webView.load(URLRequest(url: URL(string: "\(backendHost)/")!))
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async {
                self?.showError(L("Das Backend war auf Port \(backendPort) nicht erreichbar.",
                                  "The backend did not respond on port \(backendPort)."))
            }
        }
    }

    // MARK: - WKUIDelegate / WKNavigationDelegate

    /// Externe Links (z. B. DOI- oder OA-URLs) im Standardbrowser öffnen.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url,
           navigationAction.navigationType == .linkActivated,
           !url.absoluteString.hasPrefix(backendHost) {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    /// `target="_blank"`-Links (z. B. Landing-Pages) im Standardbrowser öffnen.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    /// `<input type="file">` (z. B. BibTeX- oder RIS-Datei laden) als nativer Dialog.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.prompt = L("Öffnen", "Open")
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    /// `window.confirm()` aus der Web-Oberfläche als nativer Dialog.
    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = appDisplayName
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: L("Abbrechen", "Cancel"))
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    /// `window.alert()` aus der Web-Oberfläche als nativer Dialog.
    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = appDisplayName
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
        completionHandler()
    }

    // MARK: - Hilfen

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = appDisplayName
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

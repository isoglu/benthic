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
import FoundationModels  // schwach gelinkt (build_app.sh): erst ab macOS 26 vorhanden, mit Apple Intelligence
import Vision

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

/// Stützt ein Abschnitt aus einer Quelle eine Aussage? Ein Hinweis des Sprachmodells auf diesem Mac (Apple Foundation
/// Models, ab macOS 26 mit eingeschalteter Apple Intelligence), nichts verlässt den Mac. Kein Urteil: Die Seite zeigt
/// den Abschnitt daneben, entscheiden muss, wer liest. Ohne Modell meldet die Brücke „unavailable“, und die Seite
/// blendet die Funktion aus. Die Seite ruft
/// window.webkit.messageHandlers.benthicJudge.postMessage({id, claim, passage}) oder ({id, probe: true});
/// die Antwort kommt über window.benthicJudged({id, verdict, reason}) oder ({id, available}) oder ({id, error}).
/// Strukturierte Ausgabe über DynamicGenerationSchema: Das Makro @Generable braucht Xcode, gebaut wird ohne.
final class JudgeBridge: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let id = body["id"] as? String else { return }
        guard #available(macOS 26.0, *) else {
            reply(["id": id, "error": "unsupported"])
            return
        }
        Task { await self.handle(id: id, body: body) }
    }

    @available(macOS 26.0, *)
    private func handle(id: String, body: [String: Any]) async {
        if case .unavailable(let reason) = SystemLanguageModel.default.availability {
            reply(["id": id, "error": "unavailable", "reason": String(describing: reason)])
            return
        }
        if body["probe"] as? Bool == true {
            reply(["id": id, "available": true])
            return
        }
        guard let claim = body["claim"] as? String, let passage = body["passage"] as? String else { return }
        do {
            let verdict = DynamicGenerationSchema(name: "Verdict", description: "How far the passage supports the sentence",
                                                  anyOf: ["supports", "partly", "no", "unclear"])
            let root = DynamicGenerationSchema(name: "Judgement", description: "Support of a sentence by a passage", properties: [
                DynamicGenerationSchema.Property(name: "verdict", description: "supports, partly, no or unclear", schema: verdict),
                DynamicGenerationSchema.Property(name: "reason", description: "One short sentence in the language of the sentence",
                                                 schema: DynamicGenerationSchema(type: String.self)),
            ])
            let schema = try GenerationSchema(root: root, dependencies: [])
            let session = LanguageModelSession(instructions: """
                You check academic writing. You get a sentence from a student's paper and a passage from the source \
                it cites. Say whether the passage supports the sentence: "supports" if it states the same claim, \
                "partly" if it supports only part of it or a weaker version, "no" if it does not support it or says \
                something else, "unclear" if the passage is too short or off topic to tell. Judge only from the \
                passage, never from your own knowledge. Give one short reason.
                """)
            let prompt = "Sentence: \(claim.prefix(600))\n\nPassage: \(passage.prefix(2400))"
            let response = try await session.respond(to: prompt, schema: schema)
            let v = try response.content.value(String.self, forProperty: "verdict")
            let r = try response.content.value(String.self, forProperty: "reason")
            reply(["id": id, "verdict": v, "reason": r])
        } catch {
            reply(["id": id, "error": "failed"])
        }
    }

    private func reply(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj),
              let json = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("window.benthicJudged && window.benthicJudged(\(json))", completionHandler: nil)
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

// MARK: - Grimpo, der Begleiter auf dem Schreibtisch

/// Ein Panel, das Tastatur annehmen darf, ohne die App nach vorn zu holen (randlose Panels können sonst nicht key werden).
final class PetPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// Grimpo (static/pet.html) schwebt durchsichtig über allen Fenstern, auf jedem Schreibtisch. Zugeklappt ist das Fenster
/// so groß wie Grimpo, damit es nichts darunter verdeckt; aufgeklappt wächst es nach links oben, Grimpo bleibt stehen.
/// Die Seite ruft window.webkit.messageHandlers.benthicPet.postMessage({action, ...}):
///   expand {expanded}, move {dx, dy}, screenshot {id}, frontTab {id}, open {view, text}, menu, hide;
/// Antworten mit id kommen über window.benthicPetReply({id, ...}). Das Hauptfenster nutzt denselben Namen für
///   show, hide und state (Einstellungen).
final class PetController: NSObject, WKScriptMessageHandler, NSMenuDelegate {
    static let visibleKey = "BenthicPetVisible"
    static let originKey = "BenthicPetOrigin"
    private let collapsed = NSSize(width: 112, height: 112)
    private let expanded = NSSize(width: 380, height: 520)
    private var panel: PetPanel?
    private var webView: WKWebView?
    /// Hauptfenster nach vorn holen und eine Ansicht zeigen (gesetzt von AppDelegate).
    var openApp: ((String, String) -> Void)?
    /// Benachrichtigt das Menü über Änderungen der Sichtbarkeit.
    var onChange: (() -> Void)?

    var isVisible: Bool { panel?.isVisible ?? false }

    /// Beim Start: sichtbar, außer man hat Grimpo ausgeblendet.
    func restore() {
        if UserDefaults.standard.object(forKey: Self.visibleKey) as? Bool ?? true { show() }
    }

    func show() {
        if panel == nil { build() }
        panel?.orderFrontRegardless()
        UserDefaults.standard.set(true, forKey: Self.visibleKey)
        onChange?()
    }

    func hide() {
        panel?.orderOut(nil)
        UserDefaults.standard.set(false, forKey: Self.visibleKey)
        onChange?()
    }

    @objc func toggle(_ sender: Any?) { isVisible ? hide() : show() }

    private func build() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var origin = NSPoint(x: screen.maxX - collapsed.width - 24, y: screen.minY + 24)
        if let saved = UserDefaults.standard.string(forKey: Self.originKey) {
            let p = NSPointFromString(saved)
            if NSScreen.screens.contains(where: { $0.visibleFrame.insetBy(dx: -40, dy: -40).contains(p) }) { origin = p }
        }
        let p = PetPanel(contentRect: NSRect(origin: origin, size: collapsed),
                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isFloatingPanel = true
        p.level = .floating
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.becomesKeyOnlyIfNeeded = true
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.title = "Grimpo"
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "benthicPet")
        let wv = WKWebView(frame: NSRect(origin: .zero, size: collapsed), configuration: config)
        wv.setValue(false, forKey: "drawsBackground")  // durchsichtig: nur Grimpo und sein Feld sind zu sehen
        wv.autoresizingMask = [.width, .height]
        p.contentView = wv
        wv.load(URLRequest(url: URL(string: "\(backendHost)/pet")!))
        panel = p
        webView = wv
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        let id = body["id"] as? String ?? ""
        switch action {
        case "show": show()
        case "hide": hide()
        case "state":
            message.webView?.evaluateJavaScript("window.benthicPetState && window.benthicPetState(\(isVisible))", completionHandler: nil)
        case "expand": resize(to: (body["expanded"] as? Bool ?? false) ? expanded : collapsed)
        case "move": move(dx: body["dx"] as? Double ?? 0, dy: body["dy"] as? Double ?? 0)
        case "screenshot": screenshot(id)
        case "frontTab": frontTab(id)
        case "open": openApp?(body["view"] as? String ?? "verify", body["text"] as? String ?? "")
        case "menu": showMenu()
        default: break
        }
    }

    private func resize(to size: NSSize) {
        guard let p = panel else { return }
        var frame = NSRect(x: p.frame.maxX - size.width, y: p.frame.minY, width: size.width, height: size.height)
        if let visible = (p.screen ?? NSScreen.main)?.visibleFrame {  // aufgeklappt ganz auf dem Bildschirm
            frame.origin.x = min(max(frame.minX, visible.minX), visible.maxX - frame.width)
            frame.origin.y = min(max(frame.minY, visible.minY), visible.maxY - frame.height)
        }
        p.setFrame(frame, display: true)
    }

    private func move(dx: Double, dy: Double) {
        guard let p = panel else { return }
        let origin = NSPoint(x: p.frame.minX + dx, y: p.frame.minY - dy)  // Bildschirm-y wächst in AppKit nach oben
        p.setFrameOrigin(origin)
        let anchor = NSPoint(x: p.frame.maxX - collapsed.width, y: p.frame.minY)
        UserDefaults.standard.set(NSStringFromPoint(anchor), forKey: Self.originKey)
    }

    private func reply(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj), let json = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("window.benthicPetReply && window.benthicPetReply(\(json))", completionHandler: nil)
        }
    }

    /// Bildschirmfoto eines Bereichs (macOS-eigene Auswahl), dann Texterkennung auf dem Mac (Vision).
    private func screenshot(_ id: String) {
        let folder = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/\(dataFolderName)/clips")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let file = folder.appendingPathComponent("grimpo-\(stamp).png")
        panel?.orderOut(nil)  // Grimpo nicht mitfotografieren
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            proc.arguments = ["-i", "-x", file.path]
            try? proc.run()
            proc.waitUntilExit()
            DispatchQueue.main.async { self?.panel?.orderFrontRegardless() }
            guard FileManager.default.fileExists(atPath: file.path) else {
                self?.reply(["id": id, "error": "cancelled"])
                return
            }
            let text = Self.recognize(file)
            self?.reply(["id": id, "path": file.path, "text": text])
        }
    }

    private static func recognize(_ file: URL) -> String {
        guard let image = NSImage(contentsOf: file), let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["de-DE", "en-US"]
        try? VNImageRequestHandler(cgImage: cg).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    }

    /// Adresse und Titel des vordersten Browser-Tabs (Safari, Chrome, Arc, Edge, Brave), per AppleScript.
    private func frontTab(_ id: String) {
        let browsers = ["com.apple.Safari": "safari", "com.google.Chrome": "chromium", "company.thebrowser.Browser": "chromium",
                        "com.microsoft.edgemac": "chromium", "com.brave.Browser": "chromium"]
        let running = NSWorkspace.shared.runningApplications
        let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
        guard let bid = browsers[front] != nil ? front
                : running.first(where: { browsers[$0.bundleIdentifier ?? ""] != nil && $0.isActive == false })?.bundleIdentifier,
              let kind = browsers[bid] else {
            reply(["id": id, "error": "nobrowser"])
            return
        }
        let script = kind == "safari"
            ? "tell application id \"\(bid)\" to return {URL, name} of current tab of front window"
            : "tell application id \"\(bid)\" to return {URL, title} of active tab of front window"
        var error: NSDictionary?
        let result = NSAppleScript(source: script)?.executeAndReturnError(&error)
        if let error, (error[NSAppleScript.errorNumber] as? Int) == -1743 {
            reply(["id": id, "error": "permission"])
            return
        }
        guard let result, result.numberOfItems >= 2,
              let url = result.atIndex(1)?.stringValue, let title = result.atIndex(2)?.stringValue else {
            reply(["id": id, "error": "notab"])
            return
        }
        reply(["id": id, "url": url, "title": title])
    }

    private func showMenu() {
        let menu = NSMenu()
        let open = NSMenuItem(title: L("Benthic öffnen", "Open Benthic"), action: #selector(openMain(_:)), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        menu.addItem(NSMenuItem.separator())
        let hideItem = NSMenuItem(title: L("Grimpo ausblenden", "Hide Grimpo"), action: #selector(hideFromMenu(_:)), keyEquivalent: "")
        hideItem.target = self
        menu.addItem(hideItem)
        if let wv = webView {
            menu.popUp(positioning: nil, at: wv.convert(NSPoint(x: wv.bounds.maxX - 75, y: 120), to: wv), in: wv)
        }
    }

    @objc private func openMain(_ sender: Any?) { openApp?("verify", "") }
    @objc private func hideFromMenu(_ sender: Any?) { hide() }
}

/// Prüfbericht als PDF: die Seite /report/<id> des Backends, über macOS gedruckt (A4, ohne Ränder: Die Seite
/// verteilt sich selbst auf A4-Blätter mit Deckblatt, Kopf- und Fußzeilen, siehe report.py).
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
        let config = WKWebViewConfiguration()
        if #available(macOS 13.3, *) { config.preferences.shouldPrintBackgrounds = true }  // Deckblatt und Flächen
        let view = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
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
        info.topMargin = 0
        info.bottomMargin = 0
        info.leftMargin = 0
        info.rightMargin = 0
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isVerticallyCentered = false
        info.isHorizontallyCentered = false
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
    private let judgeBridge = JudgeBridge()
    private let pet = PetController()
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
        config.userContentController.add(judgeBridge, name: "benthicJudge")
        config.userContentController.add(pet, name: "benthicPet")
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
        judgeBridge.webView = webView
        pet.openApp = { [weak self] view, text in self?.showView(view, text: text) }
        pet.onChange = { [weak self] in self?.buildMenu() }
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
        windowMenu.addItem(NSMenuItem.separator())
        let petItem = NSMenuItem(title: L("Grimpo auf dem Schreibtisch", "Grimpo on the Desktop"),
                                 action: #selector(PetController.toggle(_:)), keyEquivalent: "")
        petItem.target = pet
        petItem.state = pet.isVisible ? .on : .off
        windowMenu.addItem(petItem)
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

    /// Für Grimpo: Hauptfenster nach vorn, eine Ansicht zeigen, bei „Prüfen“ den Text (etwa DOIs) einsetzen.
    private func showView(_ view: String, text: String) {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        let quote: (String) -> String = { value in
            guard let data = try? JSONSerialization.data(withJSONObject: [value]), let json = String(data: data, encoding: .utf8) else { return "\"\"" }
            return String(json.dropFirst().dropLast())
        }
        var js = "window.showView && showView(\(quote(view)));"
        if !text.isEmpty {
            js += "var t = document.getElementById('verify-input'); if (t) { t.value = \(quote(text)); if (window.runPreview) runPreview(); }"
        }
        webView.evaluateJavaScript(js, completionHandler: nil)
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
                        self.pet.restore()  // Grimpo braucht das Backend für seine Seite
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

// Takes crisp screenshots of the Benthic interface with an offscreen WKWebView.
// Usage: snapshot <url> <out.png> <width> <height> <wait-seconds> [javascript]
// The JavaScript runs once the page has loaded; the snapshot follows after <wait-seconds>.
import AppKit
import WebKit

let args = CommandLine.arguments
guard args.count >= 6, let w = Double(args[3]), let h = Double(args[4]), Double(args[5]) != nil else {
    FileHandle.standardError.write("usage: snapshot url out.png width height wait [js]\n".data(using: .utf8)!)
    exit(2)
}
struct Job {
    static let url = URL(string: CommandLine.arguments[1])!
    static let out = URL(fileURLWithPath: CommandLine.arguments[2])
    static let wait = Double(CommandLine.arguments[5]) ?? 1
    static let script = CommandLine.arguments.count >= 7 ? CommandLine.arguments[6] : ""
}

final class Shooter: NSObject, WKNavigationDelegate {
    let window: NSWindow
    let web: WKWebView
    init(width: Double, height: Double) {
        let rect = NSRect(x: -10000, y: -10000, width: width, height: height)
        window = NSWindow(contentRect: rect, styleMask: [.borderless], backing: .buffered, defer: false)
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        super.init()
        window.contentView = web
        window.orderBack(nil)
        web.navigationDelegate = self
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript(Job.script.isEmpty ? "0" : Job.script) { _, error in
            if let error { FileHandle.standardError.write("js: \(error)\n".data(using: .utf8)!) }
            DispatchQueue.main.asyncAfter(deadline: .now() + Job.wait) { self.shoot() }
        }
    }
    func shoot() {
        let cfg = WKSnapshotConfiguration()
        cfg.afterScreenUpdates = true
        web.takeSnapshot(with: cfg) { image, error in
            guard let image, let tiff = image.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:]) else {
                FileHandle.standardError.write("snapshot failed: \(String(describing: error))\n".data(using: .utf8)!)
                exit(1)
            }
            try? png.write(to: Job.out)
            exit(0)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let shooter = Shooter(width: w, height: h)
shooter.web.load(URLRequest(url: Job.url))
DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
    FileHandle.standardError.write("timeout\n".data(using: .utf8)!)
    exit(1)
}
app.run()

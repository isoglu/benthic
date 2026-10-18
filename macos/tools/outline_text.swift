// Outlines a string in a macOS system face (SF Pro, or New York with "serif") as SVG path
// data, so the wordmark in macos/design/*.svg renders the same in every SVG renderer
// (CoreSVG and browsers without the system fonts cannot reach them by name).
//
// Build:  swiftc -O macos/tools/outline_text.swift -o .swifttmp/outline_text
// Use:    .swifttmp/outline_text Benthic 58 semibold -1.4 [sans|serif]
//         First line: advance width in px. Second line: path data, baseline at y = 0.
// Current wordmark: Lockup.svg (58, semibold, -1.4, sans).
import AppKit
import CoreText

let a = CommandLine.arguments
let text = a[1]; let size = CGFloat(Double(a[2])!)
let weights: [String: NSFont.Weight] = ["regular": .regular, "medium": .medium, "semibold": .semibold, "bold": .bold]
let base = NSFont.systemFont(ofSize: size, weight: weights[a[3]] ?? .semibold)
let serif = a.count > 5 && a[5] == "serif"
guard let desc = serif ? base.fontDescriptor.withDesign(.serif) : base.fontDescriptor,
      let font = NSFont(descriptor: desc, size: size) else { fatalError("no font") }
let tracking = CGFloat(Double(a[4]) ?? 0)
let attr = NSAttributedString(string: text, attributes: [.font: font, .kern: tracking])
let line = CTLineCreateWithAttributedString(attr)
let path = CGMutablePath()
for run in CTLineGetGlyphRuns(line) as! [CTRun] {
    let n = CTRunGetGlyphCount(run)
    var glyphs = [CGGlyph](repeating: 0, count: n); var pos = [CGPoint](repeating: .zero, count: n)
    CTRunGetGlyphs(run, CFRange(location: 0, length: n), &glyphs)
    CTRunGetPositions(run, CFRange(location: 0, length: n), &pos)
    let runFont = (CTRunGetAttributes(run) as NSDictionary)[kCTFontAttributeName] as! CTFont
    for i in 0..<n {
        if let g = CTFontCreatePathForGlyph(runFont, glyphs[i], nil) {
            // flip y: SVG y grows downward, baseline at 0
            let t = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: pos[i].x, ty: 0)
            path.addPath(g, transform: t)
        }
    }
}
var d = ""
func f(_ v: CGFloat) -> String { String(format: "%.2f", v) }
path.applyWithBlock { e in
    let p = e.pointee.points
    switch e.pointee.type {
    case .moveToPoint: d += "M\(f(p[0].x)),\(f(p[0].y)) "
    case .addLineToPoint: d += "L\(f(p[0].x)),\(f(p[0].y)) "
    case .addQuadCurveToPoint: d += "Q\(f(p[0].x)),\(f(p[0].y)) \(f(p[1].x)),\(f(p[1].y)) "
    case .addCurveToPoint: d += "C\(f(p[0].x)),\(f(p[0].y)) \(f(p[1].x)),\(f(p[1].y)) \(f(p[2].x)),\(f(p[2].y)) "
    case .closeSubpath: d += "Z "
    @unknown default: break
    }
}
let width = CTLineGetTypographicBounds(line, nil, nil, nil)
print(String(format: "%.2f", width))
print(d.trimmingCharacters(in: .whitespaces))

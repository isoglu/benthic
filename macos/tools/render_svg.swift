// Renders an SVG to a square or sized PNG with AppKit (macOS 13+ reads SVG natively).
// Usage: render_svg <in.svg> <out.png> <width> [height] [inset]
// inset = fraction of the canvas left empty on each side (0.0977 = macOS icon grid).
import AppKit

let args = CommandLine.arguments
guard args.count >= 4, let w = Int(args[3]) else {
    FileHandle.standardError.write("usage: render_svg in.svg out.png width [height] [inset]\n".data(using: .utf8)!)
    exit(2)
}
let h = args.count >= 5 ? (Int(args[4]) ?? w) : w
let inset = args.count >= 6 ? (Double(args[5]) ?? 0) : 0
guard let img = NSImage(contentsOf: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("cannot read \(args[1])\n".data(using: .utf8)!)
    exit(1)
}
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                 isPlanar: false, colorSpaceName: .deviceRGB,
                                 bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
rep.size = NSSize(width: w, height: h)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
let dx = Double(w) * inset, dy = Double(h) * inset
img.draw(in: NSRect(x: dx, y: dy, width: Double(w) - 2 * dx, height: Double(h) - 2 * dy),
         from: .zero, operation: .copy, fraction: 1)
NSGraphicsContext.restoreGraphicsState()
guard let data = rep.representation(using: .png, properties: [:]) else { exit(1) }
try data.write(to: URL(fileURLWithPath: args[2]))

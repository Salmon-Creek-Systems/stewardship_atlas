// macOS PDFKit helper (no poppler needed). usage: swift pdfcrop.swift FILE.pdf PAGE SCALE X0 Y0 X1 Y1 OUT.png   (fractions of the page, y from top)
import PDFKit
import AppKit
// args: pdf page scale x0 y0 x1 y1 (fractions, y from top) out
let a = CommandLine.arguments
let doc = PDFDocument(url: URL(fileURLWithPath: a[1]))!
let p = doc.page(at: Int(a[2])!-1)!
let sc = CGFloat(Double(a[3])!)
let fx0 = CGFloat(Double(a[4])!), fy0 = CGFloat(Double(a[5])!), fx1 = CGFloat(Double(a[6])!), fy1 = CGFloat(Double(a[7])!)
let r = p.bounds(for: .mediaBox)
let W = Int(r.width*(fx1-fx0)*sc), H = Int(r.height*(fy1-fy0)*sc)
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: W, pixelsHigh: H, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
let ctx = NSGraphicsContext(bitmapImageRep: rep)!
let cg = ctx.cgContext
cg.setFillColor(NSColor.white.cgColor); cg.fill(CGRect(x: 0, y: 0, width: W, height: H))
cg.scaleBy(x: sc, y: sc)
cg.translateBy(x: -r.width*fx0, y: -r.height*(1-fy1))
p.draw(with: .mediaBox, to: cg)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[8]))

// macOS PDFKit helper (no poppler needed). usage: swift contact.swift FILE.pdf OUTPREFIX   -> OUTPREFIX_{n}.png, 8x5 numbered page thumbnails per sheet
import PDFKit
import AppKit
let a = CommandLine.arguments
let doc = PDFDocument(url: URL(fileURLWithPath: a[1]))!
let out = a[2]; let cols = 8, rows = 5, tw = 200.0, th = 260.0
var sheet = 0
var i = 0
while i < doc.pageCount {
  let W = Double(cols)*tw, H = Double(rows)*th
  let img = NSImage(size: NSSize(width: W, height: H))
  img.lockFocus()
  NSColor.white.setFill(); NSRect(x: 0, y: 0, width: W, height: H).fill()
  for k in 0..<(cols*rows) {
    if i >= doc.pageCount { break }
    let p = doc.page(at: i)!
    let t = p.thumbnail(of: NSSize(width: tw-10, height: th-30), for: .mediaBox)
    let c = k % cols, r = k / cols
    let x = Double(c)*tw + 5, y = H - Double(r+1)*th + 25
    t.draw(in: NSRect(x: x, y: y, width: t.size.width, height: t.size.height))
    ("p\(i+1)" as NSString).draw(at: NSPoint(x: x, y: y-20), withAttributes: [.font: NSFont.boldSystemFont(ofSize: 16), .foregroundColor: NSColor.red])
    i += 1
  }
  img.unlockFocus()
  let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
  try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)_\(sheet).png"))
  sheet += 1
}

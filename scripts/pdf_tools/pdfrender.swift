// macOS PDFKit helper (no poppler needed). usage: swift pdfrender.swift FILE.pdf FIRST LAST SCALE OUTPREFIX   -> OUTPREFIX_p{N}.png (SCALE 1 = 72 dpi)
import PDFKit
import AppKit
let a = CommandLine.arguments
let doc = PDFDocument(url: URL(fileURLWithPath: a[1]))!
let first = Int(a[2])!, last = Int(a[3])!, scale = CGFloat(Double(a[4])!), out = a[5]
for i in first...min(last, doc.pageCount) {
  let p = doc.page(at: i-1)!
  let r = p.bounds(for: .mediaBox)
  let img = p.thumbnail(of: NSSize(width: r.width*scale, height: r.height*scale), for: .mediaBox)
  let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
  try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)_p\(i).png"))
}

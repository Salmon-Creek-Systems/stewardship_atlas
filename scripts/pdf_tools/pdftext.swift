// macOS PDFKit helper (no poppler needed). usage: swift pdftext.swift FILE.pdf FIRST LAST   -> page text to stdout (empty for scanned pages)
import PDFKit
let a = CommandLine.arguments
let doc = PDFDocument(url: URL(fileURLWithPath: a[1]))!
let first = Int(a[2])!, last = Int(a[3])!
print("pages:", doc.pageCount)
for i in first...min(last, doc.pageCount) {
  print("===== PAGE \(i)")
  print(doc.page(at: i-1)?.string ?? "")
}

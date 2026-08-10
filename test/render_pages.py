#!/usr/bin/env python3
"""
render_pages.py — turn PDF pages into PNG images so you can eyeball them.

This is the ground-truth check. `compare_extractors.py` tells you how much text each
library returned; it can't tell you whether that text matches the document, because it has
nothing to compare against. This script gives you the human-readable side: a picture of the
page, exactly as a PDF viewer would draw it.

    python render_pages.py                      # every PDF in ./pdfs, pages 1-2
    python render_pages.py --pages 1-3 --dpi 200
    python render_pages.py --docs some.pdf

Then put them side by side:

    results/pages/DOC-CC-001_p1.png             <- what the document actually says
    results/dumps/DOC-CC-001__pymupdf.txt       <- what PyMuPDF got out of it
    results/dumps/DOC-CC-001__pdfplumber.txt    <- what pdfplumber got out of it

Open the image and the two text files together and the disagreement is obvious: the image
shows 'Contract Value (Original)   INR 33.38 Cr'; one text file has both halves, the other
has the label and nothing after it.

With --html it also writes results/compare.html — the page image beside every extractor's
text in one scrollable view. Open it in a browser.

WHY THIS USES THE SAME LIBRARY AS THE EXTRACTOR
    Rendering the page and reading its text are two jobs done by one engine, MuPDF, which
    PyMuPDF wraps. A PDF viewer does the same thing to put pixels on your screen. That's
    the reason PyMuPDF reads these documents correctly and the pdfminer-based libraries
    don't: MuPDF is a renderer that has to resolve every glyph to draw it, so it carries
    the fallback machinery for fonts with a missing character map. pdfminer only parses the
    text layer, and when the map is missing it has nothing to fall back on.

    So this is not an independent referee — it's the same engine in its other mode. For a
    genuinely independent check, run the OCR row in compare_extractors.py: OCR reads these
    same rendered pixels with a different program entirely and never touches the fonts.
"""
import argparse, glob, html, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_pages(spec):
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", nargs="*", help="PDF paths (default: every PDF in ./pdfs)")
    ap.add_argument("--pages", default="1-2", help="page range (default: %(default)s)")
    ap.add_argument("--dpi", type=int, default=130, help="render resolution (default: %(default)s)")
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="output directory")
    ap.add_argument("--html", action="store_true", help="also write compare.html")
    a = ap.parse_args()

    import fitz  # PyMuPDF

    pages = parse_pages(a.pages)
    docs = a.docs or sorted(glob.glob(os.path.join(HERE, "pdfs", "*.pdf")))
    if not docs:
        sys.exit("No PDFs found. Put some in ./pdfs or pass --docs.")

    pagedir = os.path.join(a.out, "pages")
    os.makedirs(pagedir, exist_ok=True)
    made = []

    for path in docs:
        name = os.path.basename(path)[:-4]
        d = fitz.open(path)
        for p in pages:
            if p > len(d):
                break
            rel = f"pages/{name}_p{p}.png"
            d[p - 1].get_pixmap(dpi=a.dpi).save(os.path.join(a.out, rel))
            made.append((name, p, rel))
            print(f"  {rel}")
        d.close()

    print(f"\n{len(made)} page images in {pagedir}/")

    if a.html:
        dumpdir = os.path.join(a.out, "dumps")
        rows = []
        for name, p, rel in made:
            dumps = sorted(glob.glob(os.path.join(dumpdir, f"{name}__*.txt")))
            cols = "".join(
                f"<div class=col><h4>{html.escape(os.path.basename(f).split('__')[1][:-4])}</h4>"
                f"<pre>{html.escape(open(f).read()[:6000])}</pre></div>"
                for f in dumps)
            rows.append(f"<section><h2>{html.escape(name)} — page {p}</h2>"
                        f"<div class=row><div class=col><h4>the document</h4>"
                        f"<img src='{rel}' alt='page {p} of {name}'></div>{cols}</div></section>")
        doc = ("<!doctype html><meta charset=utf-8><title>PDF vs extracted text</title>"
               "<style>body{font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px;"
               "background:#12151a;color:#e6e9ee}h1{font-size:20px}h2{font-size:16px;"
               "border-bottom:1px solid #2a3240;padding-bottom:6px;margin-top:40px}"
               "h4{font:600 11px/1 system-ui;text-transform:uppercase;letter-spacing:.08em;"
               "color:#8d99ab;margin:0 0 8px}.row{display:flex;gap:16px;overflow-x:auto;"
               "align-items:flex-start}.col{flex:0 0 460px}img{width:100%;border:1px solid "
               "#2a3240;background:#fff}pre{white-space:pre-wrap;font:11px/1.45 ui-monospace,"
               "Menlo,monospace;background:#1a1f27;border:1px solid #2a3240;padding:10px;"
               "max-height:640px;overflow:auto;margin:0}</style>"
               "<h1>PDF vs extracted text</h1><p>Left column is the document as a PDF viewer "
               "draws it. The rest is what each library got out of it. Scroll each column "
               "sideways.</p>" + "".join(rows))
        out = os.path.join(a.out, "compare.html")
        open(out, "w").write(doc)
        print(f"wrote {out} — open it in a browser")
        if not glob.glob(os.path.join(dumpdir, "*.txt")):
            print("  (no text dumps yet — run compare_extractors.py first)")


if __name__ == "__main__":
    sys.exit(main())

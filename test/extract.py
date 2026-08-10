#!/usr/bin/env python3
"""
extract.py — print the text of one PDF.

    python extract.py pdfs/DOC-CC-001.pdf                  all pages, PyMuPDF
    python extract.py pdfs/DOC-CC-001.pdf --pages 1        page 1 only
    python extract.py pdfs/DOC-CC-001.pdf --pages 1-2
    python extract.py file.pdf --with pdfplumber           use a different library
    python extract.py file.pdf --with all                  every library, one after another
    python extract.py file.pdf --save out.txt              write to a file as well
    python extract.py file.pdf --grep "Contract Value"     only lines matching this

Default engine is PyMuPDF because it is the only one that reads every document in this
corpus correctly. Use --with to see what the others do to the same file.
"""
import argparse, os, re, sys

ENGINES = ("pymupdf", "pdfplumber", "pdfplumber_layout", "camelot_stream", "camelot_lattice", "ocr")


def parse_pages(spec, npages):
    if spec is None:
        return list(range(1, npages + 1))
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), min(int(b), npages) + 1))
    return [int(spec)]


def n_pages(path):
    import fitz
    d = fitz.open(path)
    n = len(d)
    d.close()
    return n


def extract(path, engine, pages):
    if engine == "pymupdf":
        import fitz
        d = fitz.open(path)
        out = "\n".join(d[p - 1].get_text("text", sort=True) for p in pages)
        d.close()
        return out
    if engine in ("pdfplumber", "pdfplumber_layout"):
        import pdfplumber
        layout = engine.endswith("layout")
        with pdfplumber.open(path) as pdf:
            return "\n".join((pdf.pages[p - 1].extract_text(layout=layout) or "") for p in pages)
    if engine.startswith("camelot"):
        import camelot
        flavor = engine.split("_")[1]
        tables = camelot.read_pdf(path, pages=",".join(map(str, pages)), flavor=flavor)
        return "\n".join("\n".join("\t".join(r) for r in t.df.values.tolist()) for t in tables)
    if engine == "ocr":
        import fitz
        from compare_extractors import ocr_engine
        run, name, problems = ocr_engine()
        if not run:
            return "[no OCR engine available]\n" + "\n".join(problems)
        d = fitz.open(path)
        out = "\n".join(run(d[p - 1].get_pixmap(dpi=300).tobytes("png")) for p in pages)
        d.close()
        return out
    raise SystemExit(f"unknown engine {engine!r}; choose from {', '.join(ENGINES)} or 'all'")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="path to the PDF")
    ap.add_argument("--pages", help="e.g. 1 or 1-3 (default: all pages)")
    ap.add_argument("--with", dest="engine", default="pymupdf",
                    help=f"{', '.join(ENGINES)}, or 'all' (default: pymupdf)")
    ap.add_argument("--grep", help="only print lines containing this text (case-insensitive)")
    ap.add_argument("--save", help="also write the text to this file")
    a = ap.parse_args()

    if not os.path.exists(a.pdf):
        sys.exit(f"no such file: {a.pdf}")

    total = n_pages(a.pdf)
    pages = parse_pages(a.pages, total)
    engines = list(ENGINES) if a.engine == "all" else [a.engine]

    chunks = []
    for eng in engines:
        try:
            text = extract(a.pdf, eng, pages)
        except Exception as e:
            text = f"[{eng} failed] {type(e).__name__}: {e}"
        if a.grep:
            keep = [l for l in text.splitlines() if a.grep.lower() in l.lower()]
            text = "\n".join(keep) or f"[no line contains {a.grep!r}]"
        n_chars = len(re.sub(r"\s", "", text))
        rule = "=" * 70
        header = (f"{rule}\n{eng}  ·  {os.path.basename(a.pdf)}  ·  "
                  f"pages {pages[0]}-{pages[-1]} of {total}  ·  "
                  f"{n_chars} chars\n{rule}")
        print(header)
        print(text)
        print()
        chunks.append(header + "\n" + text)

    if a.save:
        with open(a.save, "w") as f:
            f.write("\n\n".join(chunks))
        print(f"saved to {a.save}")


if __name__ == "__main__":
    sys.exit(main())

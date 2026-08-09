#!/usr/bin/env python3
"""
compare_extractors.py — head-to-head test of PDF text extractors.

Drop PDFs in ./pdfs and run it. For each document it runs every extractor available and
reports two things: how much text came out, and whether the facts we actually need
survived (a money figure, a date, a grading word, a project code).

    python compare_extractors.py                    # everything in ./pdfs, pages 1-2
    python compare_extractors.py --pages 1-3
    python compare_extractors.py --docs some.pdf other.pdf
    python compare_extractors.py --out results

Writes to --out:
    summary.md                     paste-able table
    summary.csv                    same numbers for a spreadsheet
    dumps/<DOC>__<extractor>.txt   raw text from each extractor, to eyeball side by side

Extractors
    pymupdf            PyMuPDF, plain text, sorted by position on the page
    pymupdf_tables     PyMuPDF's table finder
    pdfplumber         pdfplumber, default settings
    pdfplumber_layout  pdfplumber with layout=True
    camelot_stream     camelot, whitespace mode
    camelot_lattice    camelot, ruled-line mode (needs Ghostscript installed)
    ocr                optional, off unless an engine is installed — see OCR below

OCR
    Not required. To include the OCR row, install ONE of these (first one found wins):
        pip install paddlepaddle paddleocr                  # PaddleOCR, cross-platform
        pip install ocrmac                                  # macOS, built-in Vision engine
        brew install tesseract && pip install pytesseract   # cross-platform
    The script auto-detects and skips the row if none is present. Expect OCR to lose to
    direct extraction here — these are digital PDFs, not scans, so OCR throws away
    perfectly good characters and re-guesses them from pixels. It's included so you can
    prove that rather than assume it.

Reading the output
    Two different measurements, don't mix them up:

    'chars' / '% of best' — how much TEXT came out, compared with whichever extractor did
        best on that same document. Purely about volume.

    'fields' — whether the text contained the things we need: a money figure, grouped
        figures, a date, a grading word, a project code. Shown as Y / . / -
            Y   found it
            .   MISSED it, and another extractor on this same document did find it
            -   not present in this document at all, so nobody could find it
        Only Y-vs-. counts against an extractor. The '-' cases are the document's
        content, not the extractor's fault — a ledger page has no grading word in it.

    'fields kept' in the summary is therefore found / findable, where findable means
        "some extractor proved it was in there".

    The failure to watch for: an extractor returns the table's row LABELS and drops the
    VALUES beside them. Plausible-looking output, no data, no error, no warning.
"""
import argparse, csv, glob, os, re, statistics, sys, time, warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))

PROBES = {
    "money":   re.compile(r"(?:INR|Rs\.?|₹)\s*[\d,]+(?:\.\d+)?\s*(?:Cr|Crore|Lakh)?"
                          r"|\b\d{1,2},\d\d,\d\d,\d{3}\b"),
    "figures": re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),
    "date":    re.compile(r"\d{4}-\d\d-\d\d|\d\d/\d\d/\d{4}"
                          r"|\b\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4}\b"
                          r"|\b[A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4}\b"),
    "grading": re.compile(r"\b(?:Excellent|Very Good|Good|Satisfactory|Fair|Poor)\b"),
    "project": re.compile(r"Pkg-\d+"),
}

nonspace = lambda s: len(re.sub(r"\s", "", s or ""))


def parse_pages(spec):
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


# ---------------------------------------------------------------- extractors
def x_pymupdf(path, pages):
    import fitz
    d = fitz.open(path)
    out = [d[p - 1].get_text("text", sort=True) for p in pages if p <= len(d)]
    d.close()
    return "\n".join(out)


def x_pymupdf_tables(path, pages):
    import fitz
    d = fitz.open(path)
    parts = []
    for p in pages:
        if p > len(d):
            break
        for t in d[p - 1].find_tables().tables:
            for row in t.extract():
                parts.append("\t".join((c or "") for c in row))
    d.close()
    return "\n".join(parts)


def x_pdfplumber(path, pages, layout=False):
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for p in pages:
            if p > len(pdf.pages):
                break
            out.append(pdf.pages[p - 1].extract_text(layout=layout) or "")
    return "\n".join(out)


def x_camelot(path, pages, flavor):
    import camelot
    tables = camelot.read_pdf(path, pages=",".join(map(str, pages)), flavor=flavor)
    return "\n".join("\n".join("\t".join(r) for r in t.df.values.tolist()) for t in tables)


def _build_paddle():
    import io
    import numpy as np
    from PIL import Image
    from paddleocr import PaddleOCR
    engine = PaddleOCR(lang="en")

    def run(png):
        img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        out = engine.predict(img) if hasattr(engine, "predict") else engine.ocr(img)
        lines = []
        for page in (out or []):
            if isinstance(page, dict):                          # PaddleOCR 3.x
                lines += list(page.get("rec_texts") or [])
            else:                                               # PaddleOCR 2.x
                for det in (page or []):
                    if det and len(det) > 1:
                        lines.append(det[1][0] if isinstance(det[1], (list, tuple)) else str(det[1]))
        return "\n".join(lines)
    return run


def _build_ocrmac():
    import io
    from PIL import Image
    from ocrmac import ocrmac
    return lambda png: "\n".join(t[0] for t in ocrmac.OCR(Image.open(io.BytesIO(png))).recognize())


def _build_tesseract():
    import io, shutil
    import pytesseract
    from PIL import Image
    if not shutil.which("tesseract"):
        raise RuntimeError("pytesseract is installed but the tesseract binary is not on PATH")
    return lambda png: pytesseract.image_to_string(Image.open(io.BytesIO(png)))


def ocr_engine():
    """Return (callable(png_bytes)->str, engine_name, problems).

    Tries PaddleOCR, then macOS Vision, then tesseract, and returns the first that builds.
    `problems` explains what went wrong with the ones that didn't, because a silent skip is
    indistinguishable from "OCR is not installed" — PaddleOCR downloads its models on first
    use, and a half-finished download looks exactly like a missing library otherwise.
    """
    problems = []
    for name, build in [("PaddleOCR", _build_paddle),
                        ("ocrmac (Apple Vision)", _build_ocrmac),
                        ("tesseract", _build_tesseract)]:
        try:
            return build(), name, problems
        except ImportError:
            problems.append(f"{name}: not installed")
        except Exception as e:
            problems.append(f"{name}: installed but failed to start — {type(e).__name__}: {str(e)[:120]}")
    return None, None, problems


def x_ocr(path, pages, engine):
    import fitz
    d = fitz.open(path)
    out = []
    for p in pages:
        if p > len(d):
            break
        out.append(engine(d[p - 1].get_pixmap(dpi=300).tobytes("png")))
    d.close()
    return "\n".join(out)


def font_diagnosis(path):
    """Why the extractors disagree.

    A PDF can embed a font with no usable character map. Anything built on pdfminer
    (pdfplumber, camelot) then drops every character drawn in that font — silently, with
    no error and no warning. MuPDF falls back to the font's own internal map and gets the
    characters out. When you see a document where one extractor returns a fraction of the
    text, check this line first.
    """
    import fitz
    d = fitz.open(path)
    fonts = d.get_page_fonts(0)
    d.close()
    broken = sorted({f[4] for f in fonts if f[5] == ""})
    return ("UN-DECODABLE font: " + ", ".join(broken)) if broken else "all fonts decodable"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", nargs="*", help="PDF paths (default: every PDF in ./pdfs)")
    ap.add_argument("--pages", default="1-2", help="page range per document (default: %(default)s)")
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="output directory")
    a = ap.parse_args()

    pages = parse_pages(a.pages)
    docs = a.docs or sorted(glob.glob(os.path.join(HERE, "pdfs", "*.pdf")))
    if not docs:
        sys.exit("No PDFs found. Put some in ./pdfs or pass --docs.")

    run_ocr, ocr_name, ocr_problems = ocr_engine()
    runners = [
        ("pymupdf",           lambda p: x_pymupdf(p, pages)),
        ("pymupdf_tables",    lambda p: x_pymupdf_tables(p, pages)),
        ("pdfplumber",        lambda p: x_pdfplumber(p, pages)),
        ("pdfplumber_layout", lambda p: x_pdfplumber(p, pages, layout=True)),
        ("camelot_stream",    lambda p: x_camelot(p, pages, "stream")),
        ("camelot_lattice",   lambda p: x_camelot(p, pages, "lattice")),
    ]
    if run_ocr:
        runners.append(("ocr", lambda p: x_ocr(p, pages, run_ocr)))

    dumpdir = os.path.join(a.out, "dumps")
    os.makedirs(dumpdir, exist_ok=True)

    print(f"{len(docs)} documents · pages {a.pages} · {len(runners)} extractors")
    print(f"OCR: {ocr_name if run_ocr else 'skipped (see OCR in this file to enable)'}")
    for p in ocr_problems:
        print(f"     {p}")
    print()

    rows = []
    for path in docs:
        name = os.path.basename(path)[:-4]
        print(f"=== {name}")
        print(f"    {font_diagnosis(path)}")
        results = {}
        for label, fn in runners:
            t0 = time.time()
            try:
                text, err = fn(path), ""
            except Exception as e:
                text, err = "", f"{type(e).__name__}: {e}"[:90]
            secs = time.time() - t0
            with open(os.path.join(dumpdir, f"{name}__{label}.txt"), "w") as fh:
                fh.write(text if text else f"[NO OUTPUT] {err}")
            results[label] = dict(chars=nonspace(text), secs=secs, err=err,
                                  **{k: bool(rx.search(text)) for k, rx in PROBES.items()})

        best = max((r["chars"] for r in results.values()), default=0) or 1
        # A field only counts against an extractor if some other extractor proved it was
        # in the document. Otherwise the field simply isn't there and nobody could find it.
        findable = {k: any(r[k] for r in results.values()) for k in PROBES}
        n_findable = sum(findable.values())
        absent = [k for k, v in findable.items() if not v]
        if absent:
            print(f"    not in this document: {', '.join(absent)}")

        for label, r in results.items():
            pct = r["chars"] / best
            marks = "".join(("Y" if r[k] else ".") if findable[k] else "-" for k in PROBES)
            kept = sum(1 for k in PROBES if findable[k] and r[k])
            note = "  <-- crashed" if r["err"] else ("  <-- lost text" if pct < 0.9 else "")
            print(f"    {label:19s} {r['chars']:6d} chars {pct:6.0%}  fields[{marks}] "
                  f"{kept}/{n_findable} kept {r['secs']:5.2f}s{note}")
            rows.append(dict(document=name, extractor=label, nonspace_chars=r["chars"],
                             pct_of_best=round(pct, 3), fields_kept=kept, fields_findable=n_findable,
                             seconds=round(r["secs"], 2), error=r["err"],
                             **{k: ("yes" if r[k] else "no") if findable[k] else "n/a" for k in PROBES}))
        print()

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by_x = {}
    for r in rows:
        by_x.setdefault(r["extractor"], []).append(r)

    with open(os.path.join(a.out, "summary.md"), "w") as f:
        f.write("# PDF extractor comparison\n\n")
        f.write(f"{len(docs)} documents, pages `{a.pages}`. "
                f"OCR: {ocr_name if run_ocr else '_no engine installed, row skipped_'}\n\n")
        f.write("Two separate measurements — don't average them together:\n\n"
                "- **text recovered** — `% of best` compares an extractor against whichever one did "
                "best **on that same document**. Volume only. Whitespace excluded.\n"
                "- **fields kept** — of the things we actually need (a money figure, grouped figures, "
                "a date, a grading word, a project code), how many survived. Written as "
                "`found / findable`, where *findable* means some extractor proved it was in the "
                "document. A field no extractor found is marked `n/a` and counts against nobody — "
                "a ledger page contains no grading word, and that isn't the extractor's fault.\n\n")
        f.write("## Overall\n\n| extractor | text recovered | fields kept | avg secs |\n|---|---:|---:|---:|\n")
        for label, rs in sorted(by_x.items(), key=lambda kv: (
                -sum(r["fields_kept"] for r in kv[1]),
                -statistics.mean(r["pct_of_best"] for r in kv[1]))):
            kept, findable = sum(r["fields_kept"] for r in rs), sum(r["fields_findable"] for r in rs)
            f.write(f"| `{label}` | {statistics.mean(r['pct_of_best'] for r in rs):.0%} | "
                    f"{kept}/{findable} ({kept / max(findable, 1):.0%}) | "
                    f"{statistics.mean(r['seconds'] for r in rs):.2f} |\n")
        f.write("\n## Per document\n\n")
        cols = list(PROBES)
        for path in docs:
            name = os.path.basename(path)[:-4]
            drows = [r for r in rows if r["document"] == name]
            if not drows:
                continue
            f.write(f"### {name}\n\n_{font_diagnosis(path)}_\n\n")
            f.write("| extractor | chars | % of best | " + " | ".join(cols) + " | kept | secs |\n"
                    "|---|---:|---:|" + ":-:|" * len(cols) + "---:|---:|\n")
            for r in drows:
                cells = " | ".join("**no**" if r[c] == "no" else r[c] for c in cols)
                f.write(f"| `{r['extractor']}` | {r['nonspace_chars']} | {r['pct_of_best']:.0%} | "
                        f"{cells} | {r['fields_kept']}/{r['fields_findable']} | {r['seconds']:.2f} |\n")
            f.write("\n")

    print(f"wrote {a.out}/summary.md and summary.csv · {len(rows)} text dumps in {dumpdir}/")


if __name__ == "__main__":
    sys.exit(main())

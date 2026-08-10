#!/usr/bin/env python3
"""
coverage_audit.py — sweep every page of every PDF and flag anything that might be lost.

The bake-off in compare_extractors.py answers "which library is best on a few documents".
That is not the same question as "is anything being silently dropped anywhere in the
corpus", and passing the first tells you very little about the second. This script asks the
second question, over everything, without reference to any answer key.

    python coverage_audit.py --root ../../BITS-Hackathon-Dataset/documents
    python coverage_audit.py --root /path/to/pdfs --ocr-check 8
    python coverage_audit.py --root ... --out results/coverage

What it checks, per page:

  thin / empty pages      A page yielding almost no text is either genuinely blank or a
                          silent extraction failure. Both need eyeballing; you cannot tell
                          which from a character count.

  text locked in images   A raster image can contain words — seals, stamps, letterheads,
                          scanned inserts. That text is NOT in the PDF's text layer, so no
                          text extractor can reach it, ever. Only OCR can. This is the
                          failure that looks like success: extraction "worked", and the
                          words on the seal were never available to begin with.

  un-decodable fonts      A font embedded without a character map. pdfminer-based libraries
                          (pdfplumber, camelot) drop every character drawn in it. PyMuPDF
                          recovers it. Counted here so you know how much of the corpus
                          depends on that difference.

  letter-spaced text      'C O N F I D E N T I A L' is one word drawn with wide spacing.
                          It extracts faithfully but breaks any search for the word itself,
                          so it is an extraction success and a parsing trap.

  wrapped cell values     A value too long for its column continues on the next line, which
                          splits it in plain text. Detected as short continuation lines that
                          are indented well past the left margin.

With --ocr-check N it OCRs N pages that contain images and reports words OCR found that the
text layer does not, which measures the image-locked text directly rather than guessing.
OCR is slow (~100s/page), so keep N small.
"""
import argparse, collections, csv, glob, json, os, re, sys, warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))

LETTER_SPACED = re.compile(r"(?:\b\w\s){4,}\w\b")     # 'C O N F I D'
WORD = re.compile(r"[A-Za-z]{2,}")


def audit_page(pg):
    text = pg.get_text("text", sort=True)
    words = len(text.split())
    lines = [l for l in text.splitlines() if l.strip()]
    # a wrapped cell value looks like a short line indented far from the margin
    wrapped = sum(1 for l in lines
                  if len(l) - len(l.lstrip()) > 24 and len(l.strip()) < 40 and l.strip()[-1:] not in ".:;")
    return dict(words=words, chars=len(re.sub(r"\s", "", text)),
                images=len(pg.get_images()), drawings=len(pg.get_drawings()),
                letter_spaced=len(LETTER_SPACED.findall(text)), wrapped_lines=wrapped,
                text=text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.join(HERE, "pdfs"),
                    help="folder of PDFs, searched recursively (default: ./pdfs)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "coverage"))
    ap.add_argument("--thin", type=int, default=15, help="flag pages with fewer words than this")
    ap.add_argument("--ocr-check", type=int, default=0, metavar="N",
                    help="OCR N image-bearing pages to measure text locked in images (slow)")
    a = ap.parse_args()

    import fitz

    pdfs = sorted(glob.glob(os.path.join(a.root, "**", "*.pdf"), recursive=True))
    if not pdfs:
        sys.exit(f"no PDFs under {a.root}")
    os.makedirs(a.out, exist_ok=True)

    rows, thin, image_pages, badfont, spaced, wrapped = [], [], [], [], [], []
    by_type = collections.Counter()
    total_pages = 0

    print(f"auditing {len(pdfs)} PDFs under {a.root}\n")
    for path in pdfs:
        doc_type = os.path.basename(os.path.dirname(path))
        name = os.path.basename(path)[:-4]
        d = fitz.open(path)
        encs = {f[4] for f in d.get_page_fonts(0) if f[5] == ""}
        if encs:
            badfont.append((doc_type, name, sorted(encs)))
        for i, pg in enumerate(d):
            r = audit_page(pg)
            total_pages += 1
            by_type[doc_type] += 1
            rec = dict(doc_type=doc_type, document=name, page=i + 1,
                       words=r["words"], chars=r["chars"], images=r["images"],
                       drawings=r["drawings"], letter_spaced=r["letter_spaced"],
                       wrapped_lines=r["wrapped_lines"], undecodable_font=bool(encs))
            rows.append(rec)
            if r["words"] < a.thin:
                thin.append(rec)
            if r["images"]:
                image_pages.append(rec)
            if r["letter_spaced"]:
                spaced.append(rec)
            if r["wrapped_lines"]:
                wrapped.append(rec)
        d.close()

    with open(os.path.join(a.out, "pages.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(pdfs)} documents · {total_pages} pages · "
          f"{sum(r['words'] for r in rows):,} words extracted\n")
    print(f"  thin/empty pages (<{a.thin} words) : {len(thin):5d}"
          f"   <- inspect these, a silent failure looks identical to a blank page")
    print(f"  pages containing images           : {len(image_pages):5d}"
          f"   <- any words in them are unreachable by ANY text extractor")
    print(f"  documents with un-decodable fonts : {len(badfont):5d}"
          f"   <- pdfminer-based libraries lose these; PyMuPDF does not")
    print(f"  pages with letter-spaced text     : {len(spaced):5d}"
          f"   <- extracts fine, defeats naive keyword search")
    print(f"  pages with wrapped-looking values : {len(wrapped):5d}"
          f"   <- a value continued on the next line splits in plain text")

    print("\nimage-bearing pages by document type:")
    ic = collections.Counter(r["doc_type"] for r in image_pages)
    for t, n in ic.most_common():
        print(f"  {t:34s} {n:5d} of {by_type[t]:5d} pages")

    if thin:
        print(f"\nthinnest pages (check by eye — render them with render_pages.py):")
        for r in sorted(thin, key=lambda r: r["words"])[:10]:
            print(f"  {r['document']:24s} p{r['page']:<3d} {r['words']:4d} words  "
                  f"{r['images']} images  {r['drawings']} drawings")

    ocr_report = []
    if a.ocr_check and image_pages:
        from compare_extractors import ocr_engine
        run, engine, problems = ocr_engine()
        if not run:
            print("\n--ocr-check requested but no OCR engine:")
            for p in problems:
                print("   ", p)
        else:
            print(f"\nOCR cross-check on {a.ocr_check} image-bearing pages using {engine} "
                  f"(~100s each, be patient)")
            picks = image_pages[:: max(1, len(image_pages) // a.ocr_check)][:a.ocr_check]
            for r in picks:
                path = next(p for p in pdfs if os.path.basename(p)[:-4] == r["document"])
                d = fitz.open(path)
                pg = d[r["page"] - 1]
                layer = set(w.lower() for w in WORD.findall(pg.get_text("text")))
                seen = set(w.lower() for w in WORD.findall(run(pg.get_pixmap(dpi=300).tobytes("png"))))
                d.close()
                only_ocr = sorted(seen - layer)
                ocr_report.append(dict(document=r["document"], page=r["page"],
                                       words_only_ocr_found=only_ocr))
                print(f"  {r['document']} p{r['page']}: {len(only_ocr)} words OCR found that the "
                      f"text layer does not{': ' + ', '.join(only_ocr[:8]) if only_ocr else ''}")
            json.dump(ocr_report, open(os.path.join(a.out, "ocr_crosscheck.json"), "w"), indent=1)

    with open(os.path.join(a.out, "summary.md"), "w") as f:
        f.write("# Extraction coverage audit\n\n")
        f.write(f"`{len(pdfs)}` documents, `{total_pages}` pages, "
                f"`{sum(r['words'] for r in rows):,}` words extracted with PyMuPDF.\n\n")
        f.write("This audit deliberately does not use the sample answer key. It asks whether "
                "anything is being dropped anywhere, which a score against 25 known questions "
                "cannot tell you.\n\n")
        f.write("| check | pages | why it matters |\n|---|---:|---|\n")
        f.write(f"| thin or empty (<{a.thin} words) | {len(thin)} | a silent failure and a blank "
                "page look identical from a character count |\n")
        f.write(f"| contains images | {len(image_pages)} | words inside a raster image are not in "
                "the text layer and no text extractor can reach them — OCR only |\n")
        f.write(f"| un-decodable font (documents) | {len(badfont)} | pdfminer-based libraries drop "
                "this text silently; PyMuPDF recovers it |\n")
        f.write(f"| letter-spaced text | {len(spaced)} | extracts correctly but breaks keyword "
                "search for the spaced word |\n")
        f.write(f"| wrapped-looking values | {len(wrapped)} | a value continued on the next line is "
                "split in plain text |\n\n")
        f.write("## Image-bearing pages by document type\n\n| document type | image pages | total pages |\n|---|---:|---:|\n")
        for t, n in ic.most_common():
            f.write(f"| {t} | {n} | {by_type[t]} |\n")
        if ocr_report:
            f.write("\n## OCR cross-check\n\nWords OCR read from the page that the text layer does "
                    "not contain. These are the words locked inside images.\n\n")
            for r in ocr_report:
                ws = ", ".join(r["words_only_ocr_found"][:14]) or "_none_"
                f.write(f"- **{r['document']} p{r['page']}** — {len(r['words_only_ocr_found'])}: {ws}\n")
    print(f"\nwrote {a.out}/summary.md and pages.csv")


if __name__ == "__main__":
    sys.exit(main())

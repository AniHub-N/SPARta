# Extractor bake-off

Which PDF library actually gets the data out of the hackathon documents. Run it yourself,
don't take anyone's word for it.

## Run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python compare_extractors.py
```

Takes about 15 seconds. Results land in `results/`:

- `summary.md` — the table, paste it anywhere
- `summary.csv` — same numbers for a spreadsheet
- `results/dumps/<DOC>__<extractor>.txt` — the actual text each library produced, so you
  can open two side by side and see what went missing

## Run one PDF and look at the output

```bash
python extract.py pdfs/DOC-CC-001.pdf                    # all pages, PyMuPDF
python extract.py pdfs/DOC-CC-001.pdf --pages 1          # one page
python extract.py file.pdf --with pdfplumber             # a different library
python extract.py file.pdf --with all                    # every library in turn
python extract.py file.pdf --grep "Contract Value"       # only matching lines
python extract.py file.pdf --save out.txt
```

`--grep` is the quickest way to see the failure. Same file, same line, two libraries:

```
$ python extract.py pdfs/DOC-CC-001.pdf --grep "Contract Value"
Contract Value (Original)           INR 33.38 Cr

$ python extract.py pdfs/DOC-CC-001.pdf --grep "Contract Value" --with pdfplumber
Contract Value (Original)
```

The value is simply gone. No error, no warning.

## See it for yourself

Character counts prove nothing on their own. To check extraction against the document you
need to look at the document:

```bash
python render_pages.py --html
open results/compare.html          # Windows: start results\compare.html
```

That draws each page as an image and puts it beside every extractor's output in one view.
The page picture shows `Contract Value (Original)   INR 33.38 Cr`; the `pymupdf` column has
both halves; the `pdfplumber` column has the label and nothing after it. No statistics
needed — you can just see it.

`render_pages.py` on its own writes `results/pages/*.png` if you'd rather open the image
next to a text dump in your editor.

One caveat, stated plainly: rendering uses the same library as the winning extractor
(PyMuPDF), so it is not an independent referee — it's the same engine in its other mode.
The independent check is the OCR row, which reads the rendered pixels with a completely
different program and never touches the fonts. It agrees.

## How the PDF actually gets in

There's no upload and no service. The PDF is a file on your disk, and the library opens it
by path — the same way any program opens any file:

```python
import fitz                                   # this is PyMuPDF
doc  = fitz.open("pdfs/DOC-CC-001.pdf")       # read the file
text = doc[0].get_text("text", sort=True)     # page 1 as a string
print(text)
```

Three lines, entirely offline. "Dropping a PDF in" means copying the file into `pdfs/` —
`compare_extractors.py` looks in that folder and processes whatever it finds. Point it
somewhere else with `--docs /any/path/*.pdf`.

## What's being tested

Six configurations across three libraries:

| | |
|---|---|
| `pymupdf` | PyMuPDF, plain text, sorted by position |
| `pymupdf_tables` | PyMuPDF's table finder |
| `pdfplumber` | pdfplumber, defaults |
| `pdfplumber_layout` | pdfplumber with `layout=True` |
| `camelot_stream` | camelot, whitespace mode |
| `camelot_lattice` | camelot, ruled-line mode |

OCR is a seventh row that turns itself on if you install an engine:

```bash
pip install paddlepaddle paddleocr      # PaddleOCR — what we use
```

It's off by default because these are digital PDFs, not scans. OCR renders the page to an
image and re-guesses the characters from pixels, throwing away text that's already sitting
in the file — so it should lose to direct extraction. Worth confirming rather than
assuming, which is why the row exists. The script also accepts `ocrmac` (macOS) or
`pytesseract` if you'd rather; first one installed wins.

## The 5 PDFs in `pdfs/`

Picked to cover the cases that behave differently, not at random:

| File | Why it's here |
|---|---|
| `DOC-CC-001.pdf` | Table-style completion certificate — the case that breaks things |
| `DOC-CC-050.pdf` | Another table-style certificate, to confirm it's not a one-off |
| `DOC-CC-120.pdf` | Prose-style certificate — same document type, totally different layout |
| `DOC-GLB-2019.pdf` | Ledger, dense columns of numbers |
| `DOC-FS-2019.pdf` | Accounts, two number columns side by side |

Swap in your own with `--docs a.pdf b.pdf` or by dropping files into `pdfs/`.

## How to read the output

There are two different measurements. Don't mix them.

**`chars` / `% of best`** — how much text came out, compared with whichever extractor did
best on that same document. Volume only. It tells you nothing about whether the *useful*
text survived.

**`fields`** — whether the extracted text contained the things we need. Five probes: a
money figure, grouped figures, a date, a grading word, a project code. Each shows as:

| | |
|---|---|
| `Y` | found it |
| `.` | missed it — and another extractor on this same document *did* find it |
| `-` | not in this document at all, so nobody could find it |

Only `Y` vs `.` counts against an extractor. The `-` cases are a property of the document:
a ledger page has no grading word in it, so no extractor can be blamed for not finding
one. That's why the script prints a `not in this document:` line per document.

**`fields kept`** is therefore `found / findable`, where *findable* means some extractor
proved the thing was in there. `15/15` means an extractor recovered every field that was
recoverable across all 5 documents. This is the number to judge on.

The trap it's designed to catch: on the table-style certificates some extractors return the
row **labels** ("Contract Value (Original)", "Completion Date") and drop the **values**
beside them. Clean-looking output, no error, no warning, no data.

## Result from our run

Committed in [results/summary.md](results/summary.md), regenerate any time.

| extractor | text recovered | fields kept | avg secs |
|---|---:|---:|---:|
| `pymupdf` | 100% | **15/15** | 0.03 |
| `ocr` (PaddleOCR) | 92% | **15/15** | 107.41 |
| `pdfplumber` | 65% | 8/15 | 0.15 |
| `pdfplumber_layout` | 65% | 8/15 | 0.09 |
| `pymupdf_tables` | 20% | 6/15 | 0.13 |
| `camelot_stream` | 34% | 5/15 | 0.17 |
| `camelot_lattice` | 0% | 0/15 | 1.00 |

**PyMuPDF wins outright** — everything, instantly.

**PaddleOCR also recovers every field**, which is the interesting result: OCR works from
pixels, so the broken font that defeats pdfminer doesn't affect it at all. But it takes
~107 seconds per document against PyMuPDF's 0.03 — roughly 3,500× slower. Across 155
certificates that's about 5 hours versus 5 seconds. Useful as a second opinion when you
suspect an extraction bug; not usable as the main path.

**pdfplumber and camelot lose half the fields**, all of it on the two table-style
certificates — the documents carrying the contract value, the completion date and the
client's grading.

## Why the libraries disagree

The script prints a font diagnosis per document. On the table-style certificates you'll
see `UN-DECODABLE font`.

Those PDFs embed two fonts: a bold one for the labels and a regular one for the values.
The regular one ships without a usable character map. pdfplumber and camelot are both
built on pdfminer, which can't map those glyphs to letters, so it discards them silently.
PyMuPDF uses a different engine that falls back to the font's own internal map and
recovers the text.

That's why this isn't a settings problem. The characters never leave the PDF layer, so
nothing downstream can recover them.

## Caveats, so nobody gets misled

- `camelot_lattice` needs Ghostscript installed. Without it, it finds no tables and
  reports `0` rather than erroring. We ran it without Ghostscript, so treat that row as
  untested rather than as a result.
- The `money` probe looks for a currency-marked figure (`INR 33.38 Cr`, `Rs. 7466.00
  Lakh`, `33,38,00,000`). The accounts document states bare numbers in Lakhs with no
  currency marker, so every extractor shows `no` there. That's the probe being strict, not
  a failure.
- Only the first 2 pages of each document are read by default. Use `--pages 1-5` for more.

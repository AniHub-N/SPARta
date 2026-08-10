# Extractor bake-off

Which PDF library actually gets the data out of the hackathon documents. Run it yourself,
don't take anyone's word for it.

## Run

```bash
python3 -m venv .venv               # macOS/Linux have python3, not python
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python compare_extractors.py        # 'python' works now that the venv is active
```

Takes about 15 seconds. Results land in `results/`:

**If you get `command not found: python`** you skipped the activate step, or you're in a new
terminal — every new terminal needs `source .venv/bin/activate` again. **If you get
`ModuleNotFoundError: No module named 'fitz'`** you're running your system Python instead of
the venv's; activate it, or call it by path: `.venv/bin/python compare_extractors.py`.

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

## The Excel files

9 workbooks, 136 KB total, in `xlsx/`. Read them with **openpyxl**:

```bash
python compare_excel.py                               # benchmark -> results/excel/
python read_excel.py xlsx/Receivables_Ageing.xlsx     # inspect one workbook
python read_excel.py "xlsx/*.xlsx" --inventory        # summary of all 9
```

Results are committed, same as the PDF side:

- [results/excel/summary.md](results/excel/summary.md) — the comparison table
- [results/excel/summary.csv](results/excel/summary.csv) — per workbook, per reader
- `results/excel/sheets/` — **all 30 sheets as CSV**, so you can read the data on GitHub
  without opening Excel

All three candidate libraries read these files correctly. openpyxl wins on one point that
decides it: **it is the only one that tells you a cell holds a formula rather than a value.**
Same cell, four ways:

| reader | `D520` |
|---|---|
| `openpyxl` | `'=SUM(D2:D519)'` |
| `openpyxl(data_only=True)` | `None` |
| `pandas` | `NaN` |
| `calamine` | `''` |

These workbooks were written by a script, never opened in Excel, so **no formula result was
ever cached in the file** — 37 formula cells across the 9 workbooks, none with a value. Every
other reader sees 0 of those 37. That has two consequences:

- `data_only=True` returns `None` for every total. `None` becomes `0` in your arithmetic and
  you get a silently wrong answer.
- pandas and calamine return a blank, which is indistinguishable from an empty cell. You
  cannot tell a totals row from a data row — so you might sum a column that already contains
  its own total, or drop rows you needed.

With openpyxl the `=` prefix makes those rows identifiable, so you drop them and compute the
totals yourself. `read_excel.py` does exactly that and prints both:

```
FORMULA D520: =SUM(D2:D519)   <- no cached result; compute it yourself
NOTE 1 row(s) contain formulas (a totals row). Excluded from the 518 data rows below.
sum of 'Invoiced (INR)' over 518 data rows = 17,499,999,732
```

Speed is irrelevant — all 9 load in 0.14s with openpyxl. calamine is ~100× faster and it
buys you nothing at this size. Two other things worth knowing: dates are **strings**
(`'2019-07-06'`), not datetimes; and pandas coerces the integer money columns to floats.

## Audit the whole corpus, not 5 documents

A bake-off on 5 files says nothing about whether something is being dropped in the other
682. This sweeps every page and flags anything suspicious, with no reference to any answer
key:

```bash
python coverage_audit.py --root ../../BITS-Hackathon-Dataset/documents
python coverage_audit.py --root ... --ocr-check 4      # also OCR a few image pages
```

Our run over all 678 PDFs — 1,965 pages, 328,738 words:

| check | pages | verdict |
|---|---:|---|
| thin or empty (<15 words) | 20 | all genuine — bond page 2 and ledger continuation pages are nearly blank |
| contains images | 1,224 | seals, signatures, letterheads. **No data locked in them** — see below |
| documents with un-decodable fonts | 79 | pdfminer-based libraries lose these; PyMuPDF doesn't |
| letter-spaced text | 199 | extracts fine, breaks keyword search |
| wrapped-looking values | 745 | a value continued on the next line splits in plain text |

The images matter most, so we measured them rather than assuming: `--ocr-check` OCRs a page
and reports words OCR found that the text layer does *not* contain.

```
DOC-AR-2024 p1: 0 words OCR found that the text layer does not
DOC-CC-060  p1: 0 words
DOC-FS-2019 p3: 5 - audit, authorised, confidential, only, use
DOC-REF-116 p2: 1 - office
```

Nothing of value. `office` is seal engraving; the five on FS-2019 are the letter-spaced
`C O N F I D E N T I A L — F O R A U T H O R I S E D U S E O N L Y` banner, which **is** in
the text layer — the comparison just tokenises single letters differently. So the 1,224
image-bearing pages are decoration, and no contract value, date, name or grading is trapped
in a picture.

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

All pages of all 5 documents (37 pages total). OCR is capped at 1 page per document and is
therefore scored against PyMuPDF **on that same page**, not against the whole document —
otherwise a 1-page OCR run would be compared with 27 pages of ledger and look like a 99%
failure.

| extractor | text recovered | fields kept | avg secs |
|---|---:|---:|---:|
| `pymupdf` | 100% | **16/16** | 0.05 |
| `ocr` (PaddleOCR, 1 page) | 84% | 11/11 | 54.79 |
| `pdfplumber` | 65% | 9/16 | 0.37 |
| `pdfplumber_layout` | 65% | 9/16 | 0.37 |
| `camelot_stream` | 55% | 8/16 | 0.48 |
| `pymupdf_tables` | 21% | 6/16 | 0.33 |
| `camelot_lattice` | 0% | 0/16 | 4.14 |

**PyMuPDF wins outright** — every field on every page, in hundredths of a second.

**PaddleOCR keeps every field on the page it read**, which is the interesting part: OCR
works from pixels, so the broken font that defeats pdfminer never affects it. But at ~55
seconds a page against PyMuPDF's 0.05 it is roughly a thousand times slower, and it still
drops 16% of the characters. A second opinion when you suspect a bug; not a main path.

**pdfplumber and camelot lose 7 of 16 fields**, all on the two table-style certificates —
the documents carrying the contract value, the completion date and the client's grading.

**`camelot_lattice` is still untested, not failed** — it needs Ghostscript, which isn't
installed here, and returns 0 tables rather than erroring.

## What PyMuPDF can and cannot do

Stated plainly, from the audit rather than from the sample answers.

**Can:** every character in the text layer, on every page, in all 20 document types —
including the value column that pdfminer drops. 678 PDFs, 1,965 pages, no page failed.

**Cannot:**

| limitation | consequence |
|---|---|
| Text drawn inside a raster image | Unreachable by *any* text extractor. Here that's only seal engraving (`OFFICE SEAL`, abbreviated org names already present in the body), so nothing is lost — but if a future corpus has scanned inserts, you need OCR for them |
| Reconstruct table structure reliably | Its `find_tables` mis-splits columns and even scrambled `Quality` into `Qaulity`. Use plain text and find values by their label instead — that scored 15/15 against `find_tables`' 6/15 |
| Un-space letter-spaced text | `C O N F I D E N T I A L` comes out exactly as drawn, on 199 pages. Extraction is correct; your search for `CONFIDENTIAL` still fails |
| Rejoin a value wrapped onto two lines | 745 pages have candidates. `(JV\nPartner)` cost us the role on 1 of 155 projects until handled |

The last two are parsing problems, not extraction problems — the characters are all there.
They are also the two that will actually cost you answers.

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

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

OCR is a seventh row that turns itself on if you install an engine — see the OCR section
in `compare_extractors.py`. It's off by default because these are digital PDFs, not scans,
so OCR should lose to direct extraction. Worth confirming rather than assuming.

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

Character counts are the boring half. The `fields` column is the real test — did a money
figure, a date, a grading word and a project code survive? An extractor can return a
respectable amount of text and still have dropped every value you need.

The trap to watch for: on the table-style certificates some extractors return the row
**labels** ("Contract Value (Original)", "Completion Date") and drop the **values** next
to them. You get clean-looking output, no error, no warning, and no data.

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

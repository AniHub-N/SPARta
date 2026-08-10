# PDF extractor comparison

5 documents, pages `all`. OCR: PaddleOCR, capped at the first 1 page(s) per document and therefore scored against PyMuPDF on those same pages, not against the whole document

Two separate measurements — don't average them together:

- **text recovered** — `% of best` compares an extractor against whichever one did best **on that same document**. Volume only. Whitespace excluded.
- **fields kept** — of the things we actually need (a money figure, grouped figures, a date, a grading word, a project code), how many survived. Written as `found / findable`, where *findable* means some extractor proved it was in the document. A field no extractor found is marked `n/a` and counts against nobody — a ledger page contains no grading word, and that isn't the extractor's fault.

## Overall

| extractor | text recovered | fields kept | avg secs |
|---|---:|---:|---:|
| `pymupdf` | 100% | 16/16 (100%) | 0.05 |
| `ocr` | 84% | 11/11 (100%) | 54.79 |
| `pdfplumber` | 65% | 9/16 (56%) | 0.37 |
| `pdfplumber_layout` | 65% | 9/16 (56%) | 0.37 |
| `camelot_stream` | 55% | 8/16 (50%) | 0.48 |
| `pymupdf_tables` | 21% | 6/16 (38%) | 0.33 |
| `camelot_lattice` | 0% | 0/16 (0%) | 4.14 |

## Per document

### DOC-CC-001

_UN-DECODABLE font: MIHKDJ_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 3754 | 100% | yes | n/a | yes | yes | yes | 4/4 | 0.04 |
| `pymupdf_tables` | 804 | 21% | yes | n/a | yes | yes | yes | 4/4 | 0.28 |
| `pdfplumber` | 453 | 12% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.19 |
| `pdfplumber_layout` | 453 | 12% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.11 |
| `camelot_stream` | 338 | 9% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.18 |
| `camelot_lattice` | 0 | 0% | **no** | n/a | **no** | **no** | **no** | 0/4 | 1.64 |
| `ocr` | 1489 | 99% | yes | n/a | yes | n/a | yes | 3/3 | 62.13 |

### DOC-CC-050

_UN-DECODABLE font: BPYFOW_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 3715 | 100% | yes | yes | yes | yes | yes | 5/5 | 0.04 |
| `pymupdf_tables` | 0 | 0% | **no** | **no** | **no** | **no** | **no** | 0/5 | 0.16 |
| `pdfplumber` | 455 | 12% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.10 |
| `pdfplumber_layout` | 455 | 12% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.24 |
| `camelot_stream` | 338 | 9% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.13 |
| `camelot_lattice` | 0 | 0% | **no** | **no** | **no** | **no** | **no** | 0/5 | 1.60 |
| `ocr` | 1331 | 88% | yes | yes | yes | n/a | yes | 4/4 | 59.54 |

### DOC-CC-120

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.01 |
| `pymupdf_tables` | 0 | 0% | **no** | n/a | **no** | n/a | **no** | 0/3 | 0.04 |
| `pdfplumber` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.05 |
| `pdfplumber_layout` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.05 |
| `camelot_stream` | 691 | 86% | yes | n/a | yes | n/a | yes | 3/3 | 0.05 |
| `camelot_lattice` | 0 | 0% | **no** | n/a | **no** | n/a | **no** | 0/3 | 0.55 |
| `ocr` | 711 | 89% | yes | n/a | yes | n/a | yes | 3/3 | 52.31 |

### DOC-FS-2019

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 2808 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.04 |
| `pymupdf_tables` | 0 | 0% | n/a | **no** | **no** | n/a | n/a | 0/2 | 0.16 |
| `pdfplumber` | 2808 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.20 |
| `pdfplumber_layout` | 2808 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.18 |
| `camelot_stream` | 2042 | 73% | n/a | yes | **no** | n/a | n/a | 1/2 | 0.20 |
| `camelot_lattice` | 0 | 0% | n/a | **no** | **no** | n/a | n/a | 0/2 | 1.96 |
| `ocr` | 709 | 97% | n/a | yes | n/a | n/a | n/a | 1/1 | 54.46 |

### DOC-GLB-2019

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 12159 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.12 |
| `pymupdf_tables` | 10232 | 84% | n/a | yes | yes | n/a | n/a | 2/2 | 1.03 |
| `pdfplumber` | 12159 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 1.33 |
| `pdfplumber_layout` | 12159 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 1.25 |
| `camelot_stream` | 11815 | 97% | n/a | yes | yes | n/a | n/a | 2/2 | 1.84 |
| `camelot_lattice` | 0 | 0% | n/a | **no** | **no** | n/a | n/a | 0/2 | 14.95 |
| `ocr` | 138 | 45% | n/a | n/a | n/a | n/a | n/a | 0/0 | 45.51 |


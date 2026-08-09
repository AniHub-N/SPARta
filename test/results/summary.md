# PDF extractor comparison

5 documents, pages `1-2`. OCR: PaddleOCR

Two separate measurements — don't average them together:

- **text recovered** — `% of best` compares an extractor against whichever one did best **on that same document**. Volume only. Whitespace excluded.
- **fields kept** — of the things we actually need (a money figure, grouped figures, a date, a grading word, a project code), how many survived. Written as `found / findable`, where *findable* means some extractor proved it was in the document. A field no extractor found is marked `n/a` and counts against nobody — a ledger page contains no grading word, and that isn't the extractor's fault.

## Overall

| extractor | text recovered | fields kept | avg secs |
|---|---:|---:|---:|
| `pymupdf` | 100% | 15/15 (100%) | 0.03 |
| `ocr` | 92% | 15/15 (100%) | 107.41 |
| `pdfplumber` | 65% | 8/15 (53%) | 0.15 |
| `pdfplumber_layout` | 65% | 8/15 (53%) | 0.09 |
| `pymupdf_tables` | 20% | 6/15 (40%) | 0.13 |
| `camelot_stream` | 34% | 5/15 (33%) | 0.17 |
| `camelot_lattice` | 0% | 0/15 (0%) | 1.00 |

## Per document

### DOC-CC-001

_UN-DECODABLE font: MIHKDJ_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 3390 | 100% | yes | n/a | yes | yes | yes | 4/4 | 0.04 |
| `pymupdf_tables` | 804 | 24% | yes | n/a | yes | yes | yes | 4/4 | 0.26 |
| `pdfplumber` | 420 | 12% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.18 |
| `pdfplumber_layout` | 420 | 12% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.10 |
| `camelot_stream` | 305 | 9% | **no** | n/a | **no** | yes | **no** | 1/4 | 0.17 |
| `camelot_lattice` | 0 | 0% | **no** | n/a | **no** | **no** | **no** | 0/4 | 1.12 |
| `ocr` | 3274 | 97% | yes | n/a | yes | yes | yes | 4/4 | 130.49 |

### DOC-CC-050

_UN-DECODABLE font: BPYFOW_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 3515 | 100% | yes | yes | yes | yes | yes | 5/5 | 0.06 |
| `pymupdf_tables` | 0 | 0% | **no** | **no** | **no** | **no** | **no** | 0/5 | 0.17 |
| `pdfplumber` | 422 | 12% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.27 |
| `pdfplumber_layout` | 422 | 12% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.09 |
| `camelot_stream` | 305 | 9% | **no** | **no** | **no** | yes | **no** | 1/5 | 0.11 |
| `camelot_lattice` | 0 | 0% | **no** | **no** | **no** | **no** | **no** | 0/5 | 1.13 |
| `ocr` | 3218 | 92% | yes | yes | yes | yes | yes | 5/5 | 135.55 |

### DOC-CC-120

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.02 |
| `pymupdf_tables` | 0 | 0% | **no** | n/a | **no** | n/a | **no** | 0/3 | 0.04 |
| `pdfplumber` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.06 |
| `pdfplumber_layout` | 802 | 100% | yes | n/a | yes | n/a | yes | 3/3 | 0.05 |
| `camelot_stream` | 0 | 0% | **no** | n/a | **no** | n/a | **no** | 0/3 | 0.05 |
| `camelot_lattice` | 0 | 0% | **no** | n/a | **no** | n/a | **no** | 0/3 | 0.60 |
| `ocr` | 711 | 89% | yes | n/a | yes | n/a | yes | 3/3 | 51.54 |

### DOC-FS-2019

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 1238 | 100% | n/a | yes | n/a | n/a | n/a | 1/1 | 0.02 |
| `pymupdf_tables` | 0 | 0% | n/a | **no** | n/a | n/a | n/a | 0/1 | 0.06 |
| `pdfplumber` | 1238 | 100% | n/a | yes | n/a | n/a | n/a | 1/1 | 0.09 |
| `pdfplumber_layout` | 1238 | 100% | n/a | yes | n/a | n/a | n/a | 1/1 | 0.08 |
| `camelot_stream` | 691 | 56% | n/a | yes | n/a | n/a | n/a | 1/1 | 0.10 |
| `camelot_lattice` | 0 | 0% | n/a | **no** | n/a | n/a | n/a | 0/1 | 1.02 |
| `ocr` | 1200 | 97% | n/a | yes | n/a | n/a | n/a | 1/1 | 102.26 |

### DOC-GLB-2019

_all fonts decodable_

| extractor | chars | % of best | money | figures | date | grading | project | kept | secs |
|---|---:|---:|:-:|:-:|:-:|:-:|:-:|---:|---:|
| `pymupdf` | 1530 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.02 |
| `pymupdf_tables` | 1165 | 76% | n/a | yes | yes | n/a | n/a | 2/2 | 0.13 |
| `pdfplumber` | 1530 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.14 |
| `pdfplumber_layout` | 1530 | 100% | n/a | yes | yes | n/a | n/a | 2/2 | 0.14 |
| `camelot_stream` | 1509 | 99% | n/a | yes | yes | n/a | n/a | 2/2 | 0.44 |
| `camelot_lattice` | 0 | 0% | n/a | **no** | **no** | n/a | n/a | 0/2 | 1.14 |
| `ocr` | 1355 | 89% | n/a | yes | yes | n/a | n/a | 2/2 | 117.20 |


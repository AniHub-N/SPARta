# Extraction coverage audit

`678` documents, `1965` pages, `328,738` words extracted with PyMuPDF.

This audit deliberately does not use the sample answer key. It asks whether anything is being dropped anywhere, which a score against 25 known questions cannot tell you.

| check | pages | why it matters |
|---|---:|---|
| thin or empty (<15 words) | 20 | a silent failure and a blank page look identical from a character count |
| contains images | 1224 | words inside a raster image are not in the text layer and no text extractor can reach them — OCR only |
| un-decodable font (documents) | 79 | pdfminer-based libraries drop this text silently; PyMuPDF recovers it |
| letter-spaced text | 199 | extracts correctly but breaks keyword search for the spaced word |
| wrapped-looking values | 745 | a value continued on the next line is split in plain text |

## Image-bearing pages by document type

| document type | image pages | total pages |
|---|---:|---:|
| completion_certificate | 298 | 298 |
| tender_dossier | 288 | 288 |
| company_completion_certificate | 160 | 235 |
| reference_letter | 131 | 176 |
| performance_bond | 116 | 116 |
| cv | 78 | 78 |
| personnel_certificate | 48 | 48 |
| compliance_matrix | 38 | 59 |
| annual_report | 36 | 36 |
| financial_statement | 21 | 21 |
| iso_certificate | 10 | 10 |

## OCR cross-check

Words OCR read from the page that the text layer does not contain. These are the words locked inside images.

- **DOC-AR-2024 p1** — 0: _none_
- **DOC-CC-060 p1** — 0: _none_
- **DOC-FS-2019 p3** — 5: audit, authorised, confidential, only, use
- **DOC-REF-116 p2** — 1: office

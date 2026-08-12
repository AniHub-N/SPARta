# Running the system on any set of documents

This system is not tied to the sample company. You point it at **any folder of PDFs and
spreadsheets**, give it **any list of questions**, and it writes a file of answers. That is
exactly what's needed for judging: a fresh, unseen archive goes in, a file of numbers comes
out.

## One-time setup

Needs Python 3.10 or newer, and a Gemini (or other OpenAI-compatible) API key.

```bash
pip install -e .                 # install the system
cp .env.example .env             # then paste your API key into .env
```

In `.env`, set your key and model:

```
JAW_LLM_PROVIDER=openai_compatible
JAW_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
JAW_LLM_API_KEY=<your key here>
JAW_LLM_MODEL=gemini-2.5-flash
```

## The three commands

```bash
# 1. Read every PDF/spreadsheet in the documents folder
jaw-ingest   --source  path/to/their/documents   --output results.json

# 2. Turn that into facts (pass their document_index.csv if they provide one)
jaw-evidence --input   results.json   --output data/evidence   --document-index path/to/document_index.csv

# 3. Answer their questions -> a submission file
jaw-submit   --questions path/to/their/questions.json   --evidence-root data/evidence   --output submission.csv
```

That's the whole run. Step 3 builds the search index the first time (a few minutes, no
API cost) and reuses it after. Only step 3 spends on the API.

## What goes in and what comes out

**Questions file** — a JSON list, each entry a question with an id and its answer type:

```json
{ "questions": [
    { "qid": "Q1", "question": "Total value of all works for Client X?", "answer_type": "money" },
    { "qid": "Q2", "question": "How many works have no reference letter?", "answer_type": "count" }
] }
```

**Answers file** (`submission.csv`) — one row per question, just an id and a number:

```
question_id,answer
Q1,2008199999
Q2,2
```

Every answer is a plain number — no commas, no currency symbols, no units — which is the
format the scorer expects. Any question the system can't answer is written as `0` rather
than left blank, so nothing is ever missing.

## Checking the score yourself

If you have the correct answers, the scorer compares your file against them and reports how
close you were, using the competition's tolerance bands:

```bash
python scripts/evaluate.py submission.csv   # see the script's --help for the answers-file flag
```

## A note on the submission format

The competition's own scorer (`evaluate.py` shipped with the dataset) reads answers as
**JSONL** — one `{"qid": ..., "answer": ...}` per line — while `jaw-submit` writes **CSV**.
Converting one to the other is a few lines; confirm which the organisers want before the
final submission and we'll match it exactly.

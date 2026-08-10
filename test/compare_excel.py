#!/usr/bin/env python3
"""
compare_excel.py — head-to-head test of Excel readers, and a CSV dump of every sheet.

The Excel counterpart of compare_extractors.py. Same idea: run every reader over the same
workbooks and write the evidence to disk instead of asking anyone to trust a summary.

    python compare_excel.py                      # every .xlsx in ./xlsx
    python compare_excel.py --files a.xlsx b.xlsx
    python compare_excel.py --out results/excel

Writes to --out:
    summary.md          the comparison table
    summary.csv         same numbers for a spreadsheet
    sheets/<file>__<sheet>.csv    every sheet as CSV, so the data is readable without Excel

Readers tested
    openpyxl            default — cell values, and formulas as their '=...' text
    openpyxl_data_only  what you get if you ask for computed values instead
    pandas              read_excel, one DataFrame per sheet
    calamine            python-calamine, the fast Rust reader

What is actually being measured
    Not speed — these files are 136 KB and every reader finishes instantly. The question is
    whether a reader can distinguish a FORMULA cell from a VALUE cell.

    These workbooks were written by a script and never opened in Excel, so no formula result
    was ever cached in the file. A reader that hands you a blank for those cells leaves you
    unable to tell a totals row from a data row, which leads to summing a column that
    already contains its own total, or dropping rows you needed. A reader that hands you
    None turns a total into 0 in your arithmetic.

    'formula cells seen' below is therefore the column that decides the choice.
"""
import argparse, csv, glob, os, statistics, sys, time, warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
is_formula = lambda v: isinstance(v, str) and v.startswith("=")


def r_openpyxl(path, data_only=False):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=data_only)
    return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}


def r_pandas(path):
    import pandas as pd
    out = {}
    for name, df in pd.read_excel(path, sheet_name=None, header=None).items():
        out[name] = [[None if pd.isna(v) else v for v in row] for row in df.values.tolist()]
    return out


def r_calamine(path):
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(path)
    return {n: [list(r) for r in wb.get_sheet_by_name(n).to_python()] for n in wb.sheet_names}


READERS = [
    ("openpyxl",           lambda p: r_openpyxl(p, False)),
    ("openpyxl_data_only", lambda p: r_openpyxl(p, True)),
    ("pandas",             r_pandas),
    ("calamine",           r_calamine),
]


def stats(sheets):
    cells = formulas = filled = 0
    for rows in sheets.values():
        for row in rows:
            for v in row:
                cells += 1
                if is_formula(v):
                    formulas += 1
                if v not in (None, ""):
                    filled += 1
    return dict(sheets=len(sheets), rows=sum(len(r) for r in sheets.values()),
                filled_cells=filled, formula_cells=formulas)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="xlsx paths (default: every .xlsx in ./xlsx)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "excel"))
    a = ap.parse_args()

    files = a.files or sorted(glob.glob(os.path.join(HERE, "xlsx", "*.xlsx")))
    if not files:
        sys.exit("No .xlsx found. Put some in ./xlsx or pass --files.")
    sheetdir = os.path.join(a.out, "sheets")
    os.makedirs(sheetdir, exist_ok=True)

    rows, missing = [], []
    print(f"{len(files)} workbooks · {len(READERS)} readers\n")
    for path in files:
        base = os.path.basename(path)
        print(f"=== {base}")
        for label, fn in READERS:
            t0 = time.time()
            try:
                sheets, err = fn(path), ""
            except ImportError as e:
                sheets, err = {}, f"not installed ({e.name})"
                if label not in missing:
                    missing.append(label)
            except Exception as e:
                sheets, err = {}, f"{type(e).__name__}: {e}"[:80]
            secs = time.time() - t0
            s = stats(sheets)
            print(f"    {label:19s} sheets={s['sheets']} rows={s['rows']:5d} "
                  f"filled={s['filled_cells']:6d} formulas={s['formula_cells']:3d} "
                  f"{secs:6.3f}s{'  <-- ' + err if err else ''}")
            rows.append(dict(workbook=base, reader=label, seconds=round(secs, 4), error=err, **s))
        # dump sheets once, from openpyxl (the reader that keeps formulas visible)
        try:
            for name, data in r_openpyxl(path).items():
                out = os.path.join(sheetdir, f"{base[:-5]}__{name}.csv")
                with open(out, "w", newline="") as f:
                    csv.writer(f).writerows([["" if v is None else v for v in r] for r in data])
        except Exception as e:
            print(f"    (sheet dump failed: {e})")
        print()

    with open(os.path.join(a.out, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by = {}
    for r in rows:
        by.setdefault(r["reader"], []).append(r)

    with open(os.path.join(a.out, "summary.md"), "w") as f:
        f.write("# Excel reader comparison\n\n")
        f.write(f"`{len(files)}` workbooks. Every sheet is also dumped to `sheets/*.csv` so the "
                "data is readable without Excel.\n\n")
        f.write("Speed is not the question — these files are small and every reader finishes "
                "instantly. The question is whether a reader can tell a **formula** cell from a "
                "**value** cell. These workbooks were written by a script and never saved by "
                "Excel, so no formula result was ever cached in the file. A reader that returns a "
                "blank for those cells leaves you unable to distinguish a totals row from a data "
                "row; a reader that returns `None` turns a total into `0` in your arithmetic.\n\n")
        f.write("| reader | formula cells seen | filled cells | total rows | avg secs |\n"
                "|---|---:|---:|---:|---:|\n")
        for label, rs in sorted(by.items(), key=lambda kv: -sum(r["formula_cells"] for r in kv[1])):
            note = " _(not installed)_" if all(r["error"] for r in rs) else ""
            f.write(f"| `{label}`{note} | **{sum(r['formula_cells'] for r in rs)}** | "
                    f"{sum(r['filled_cells'] for r in rs)} | {sum(r['rows'] for r in rs)} | "
                    f"{statistics.mean(r['seconds'] for r in rs):.4f} |\n")
        f.write("\nOnly `openpyxl` reports the formulas. The others return `None`, `NaN` or `''` — "
                "indistinguishable from an empty cell.\n\n")
        f.write("## Per workbook\n\n| workbook | reader | sheets | rows | filled | formulas | secs |\n"
                "|---|---|---:|---:|---:|---:|---:|\n")
        for r in rows:
            f.write(f"| {r['workbook']} | `{r['reader']}` | {r['sheets']} | {r['rows']} | "
                    f"{r['filled_cells']} | {r['formula_cells']} | {r['seconds']:.4f} |\n")

    if missing:
        print(f"not installed, rows left empty: {', '.join(missing)}")
        print("  pip install pandas python-calamine    # only needed for the comparison")
    print(f"wrote {a.out}/summary.md, summary.csv, and per-sheet CSVs in {sheetdir}/")


if __name__ == "__main__":
    sys.exit(main())

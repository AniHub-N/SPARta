# Excel reader comparison

`9` workbooks. Every sheet is also dumped to `sheets/*.csv` so the data is readable without Excel.

Speed is not the question — these files are small and every reader finishes instantly. The question is whether a reader can tell a **formula** cell from a **value** cell. These workbooks were written by a script and never saved by Excel, so no formula result was ever cached in the file. A reader that returns a blank for those cells leaves you unable to distinguish a totals row from a data row; a reader that returns `None` turns a total into `0` in your arithmetic.

| reader | formula cells seen | filled cells | total rows | avg secs |
|---|---:|---:|---:|---:|
| `openpyxl` | **37** | 9199 | 1447 | 0.0423 |
| `openpyxl_data_only` | **0** | 9162 | 1447 | 0.0136 |
| `pandas` | **0** | 9162 | 1447 | 0.0578 |
| `calamine` | **0** | 9162 | 1447 | 0.0024 |

Only `openpyxl` reports the formulas. The others return `None`, `NaN` or `''` — indistinguishable from an empty cell.

## Per workbook

| workbook | reader | sheets | rows | filled | formulas | secs |
|---|---|---:|---:|---:|---:|---:|
| BOQ_and_Measurements_Contract_71.xlsx | `openpyxl` | 3 | 85 | 482 | 2 | 0.2328 |
| BOQ_and_Measurements_Contract_71.xlsx | `openpyxl_data_only` | 3 | 85 | 480 | 0 | 0.0079 |
| BOQ_and_Measurements_Contract_71.xlsx | `pandas` | 3 | 85 | 480 | 0 | 0.3676 |
| BOQ_and_Measurements_Contract_71.xlsx | `calamine` | 3 | 85 | 480 | 0 | 0.0077 |
| BOQ_and_Measurements_Contract_72.xlsx | `openpyxl` | 3 | 58 | 320 | 2 | 0.0071 |
| BOQ_and_Measurements_Contract_72.xlsx | `openpyxl_data_only` | 3 | 58 | 318 | 0 | 0.0063 |
| BOQ_and_Measurements_Contract_72.xlsx | `pandas` | 3 | 58 | 318 | 0 | 0.0085 |
| BOQ_and_Measurements_Contract_72.xlsx | `calamine` | 3 | 58 | 318 | 0 | 0.0007 |
| BOQ_and_Measurements_Contract_73.xlsx | `openpyxl` | 3 | 69 | 386 | 2 | 0.0080 |
| BOQ_and_Measurements_Contract_73.xlsx | `openpyxl_data_only` | 3 | 69 | 384 | 0 | 0.0069 |
| BOQ_and_Measurements_Contract_73.xlsx | `pandas` | 3 | 69 | 384 | 0 | 0.0091 |
| BOQ_and_Measurements_Contract_73.xlsx | `calamine` | 3 | 69 | 384 | 0 | 0.0008 |
| BOQ_and_Measurements_Contract_74.xlsx | `openpyxl` | 3 | 106 | 608 | 2 | 0.0095 |
| BOQ_and_Measurements_Contract_74.xlsx | `openpyxl_data_only` | 3 | 106 | 606 | 0 | 0.0085 |
| BOQ_and_Measurements_Contract_74.xlsx | `pandas` | 3 | 106 | 606 | 0 | 0.0108 |
| BOQ_and_Measurements_Contract_74.xlsx | `calamine` | 3 | 106 | 606 | 0 | 0.0011 |
| BOQ_and_Measurements_Contract_75.xlsx | `openpyxl` | 3 | 109 | 626 | 2 | 0.0092 |
| BOQ_and_Measurements_Contract_75.xlsx | `openpyxl_data_only` | 3 | 109 | 624 | 0 | 0.0089 |
| BOQ_and_Measurements_Contract_75.xlsx | `pandas` | 3 | 109 | 624 | 0 | 0.0118 |
| BOQ_and_Measurements_Contract_75.xlsx | `calamine` | 3 | 109 | 624 | 0 | 0.0011 |
| BOQ_and_Measurements_Contract_79.xlsx | `openpyxl` | 3 | 71 | 398 | 2 | 0.0075 |
| BOQ_and_Measurements_Contract_79.xlsx | `openpyxl_data_only` | 3 | 71 | 396 | 0 | 0.0075 |
| BOQ_and_Measurements_Contract_79.xlsx | `pandas` | 3 | 71 | 396 | 0 | 0.0267 |
| BOQ_and_Measurements_Contract_79.xlsx | `calamine` | 3 | 71 | 396 | 0 | 0.0009 |
| Plant_and_Machinery_Register.xlsx | `openpyxl` | 2 | 215 | 1904 | 1 | 0.0224 |
| Plant_and_Machinery_Register.xlsx | `openpyxl_data_only` | 2 | 215 | 1903 | 0 | 0.0219 |
| Plant_and_Machinery_Register.xlsx | `pandas` | 2 | 215 | 1903 | 0 | 0.0236 |
| Plant_and_Machinery_Register.xlsx | `calamine` | 2 | 215 | 1903 | 0 | 0.0025 |
| Receivables_Ageing.xlsx | `openpyxl` | 2 | 523 | 3640 | 3 | 0.0421 |
| Receivables_Ageing.xlsx | `openpyxl_data_only` | 2 | 523 | 3637 | 0 | 0.0407 |
| Receivables_Ageing.xlsx | `pandas` | 2 | 523 | 3637 | 0 | 0.0431 |
| Receivables_Ageing.xlsx | `calamine` | 2 | 523 | 3637 | 0 | 0.0048 |
| Trial_Balance_by_Year.xlsx | `openpyxl` | 8 | 211 | 835 | 21 | 0.0420 |
| Trial_Balance_by_Year.xlsx | `openpyxl_data_only` | 8 | 211 | 814 | 0 | 0.0141 |
| Trial_Balance_by_Year.xlsx | `pandas` | 8 | 211 | 814 | 0 | 0.0189 |
| Trial_Balance_by_Year.xlsx | `calamine` | 8 | 211 | 814 | 0 | 0.0018 |

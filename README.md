# Leave buckets

Small web app: upload a weekly hours CSV and a managed employee ID list. Pay codes **not** in the allowlist are treated as leave; results split into **used leave** (detail rows per employee) and **did not use leave**. For each employee who used leave, **Debits (this period)** sums hours by accrual bank using [`pay_code_mapping.py`](/Users/david/absences_tools/pay_code_mapping.py). Optionally upload a **leave bank** Excel (`.xlsx`): **Balances (import)** shows the file snapshot, **Balance vs debit** compares debits to balances when both exist.

## Local run

```bash
cd absences_tools
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Hours CSV format

No header row. Columns: `employee_id`, `hours`, `pay_code`, `date` (e.g. `3/23/2026`).

## Roster file

One employee id per line (digits only). Lines starting with `#` are ignored.

## Leave bank (optional)

Upload an `.xlsx` accrual balance report. The parser looks for a row whose column **A** is `Employee`, then reads balance columns from that row (skipping empty cells). Data rows have a numeric employee id in **A**; non-numeric rows (department headers, totals) are skipped. Balances display with two decimal places.

## Pay code → bank mapping

Edit [`pay_code_mapping.py`](/Users/david/absences_tools/pay_code_mapping.py): `PAY_CODE_TO_BANK` maps normalized pay codes to bank keys (`AL`, `COMP`, `HOLIDAY`, `SICK`, `VAC`, …) that must match your bank export headers. **`FMLA …`** codes strip the prefix and map like the base code, except **`FMLA LWOP`** (and plain **`LWOP`**) which do **not** debit banks (unpaid). Unmapped codes appear in **Parse warnings** and do not add to debits.

## Allowlist

Default codes treated as **not** leave: `REG FT`, `OT 1.5`, `CT EARN 1.5`, `GUARANTEE`, `BEREAVEMENT`.

Override with environment variable `ALLOWED_CODES` (comma-separated), e.g. `REG FT,OT 1.5,CT EARN 1.5,GUARANTEE,BEREAVEMENT,CT PAY 1.0`.

## Railway (GitHub)

1. Push this repo to GitHub and connect it in [Railway](https://railway.app).
2. Railway sets `PORT`; the included `Procfile` starts Gunicorn with uvicorn workers.
3. If the dashboard asks for a start command, use:  
   `gunicorn -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:$PORT --workers 2`
4. Keep the service private; payroll uploads are sensitive.

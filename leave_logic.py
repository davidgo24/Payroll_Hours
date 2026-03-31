"""Parse hours CSV + roster; classify non-whitelisted pay codes as leave."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from leavebank_io import parse_leavebank_workbook
from pay_code_mapping import (
    aggregate_ledger_for_leave_rows,
    build_period_debit_columns,
    build_reconciliation_rows,
)


DEFAULT_ALLOWED_CODES: frozenset[str] = frozenset(
    {
        "REG FT",
        "OT 1.5",
        "CT EARN 1.5",
        "GUARANTEE",
        "BEREAVEMENT",
    }
)


@dataclass(frozen=True)
class HoursRow:
    employee_id: str
    hours: str
    pay_code: str
    date: str
    line_number: int


def _norm_id(raw: str) -> str:
    return raw.strip()


def parse_roster(text: str) -> tuple[set[str], list[str]]:
    """Return (employee ids, warnings). Lines starting with # ignored; blanks skipped."""
    ids: set[str] = set()
    warnings: list[str] = []

    for line_num, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        eid = _norm_id(s)
        if not eid.isdigit():
            warnings.append(f"Roster line {line_num}: expected numeric employee id, got {eid!r}")
            continue
        ids.add(eid)

    return ids, warnings


def parse_hours_csv(text: str) -> tuple[list[HoursRow], list[str]]:
    """Parse hours file: no header; columns employee_id, hours, pay_code, date."""
    rows: list[HoursRow] = []
    warnings: list[str] = []
    reader = csv.reader(io.StringIO(text))
    for line_number, parts in enumerate(reader, start=1):
        if not parts or all(not p.strip() for p in parts):
            continue
        if len(parts) < 4:
            warnings.append(f"Hours line {line_number}: expected 4 columns, got {len(parts)}")
            continue
        emp, hrs, code, date_str = parts[0], parts[1], parts[2], parts[3]
        eid = _norm_id(emp)
        if not eid:
            warnings.append(f"Hours line {line_number}: empty employee id")
            continue
        rows.append(
            HoursRow(
                employee_id=eid,
                hours=hrs.strip(),
                pay_code=code.strip(),
                date=date_str.strip(),
                line_number=line_number,
            )
        )
    return rows, warnings


def _parse_hours_value(s: str) -> float | None:
    try:
        return float(s.strip().replace(",", ""))
    except ValueError:
        return None


def _parse_sort_date(date_str: str) -> tuple[int, int, int]:
    raw = date_str.strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            return (d.year, d.month, d.day)
        except ValueError:
            pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            d = datetime.strptime(raw, fmt)
            return (d.year, d.month, d.day)
        except ValueError:
            continue
    return (9999, 12, 31)


def analyze(
    hours_text: str,
    roster_text: str,
    allowed_codes: Iterable[str] | None = None,
    bank_workbook: bytes | None = None,
) -> dict:
    """
    Returns dict with:
      - roster_ids: sorted list of managed employee ids
      - used_leave: list of {employee_id, rows, total_hours} where rows sorted by date;
        total_hours is sum of leave row hours formatted with two decimals;
        optional bank: {found, columns} or {found, message} when bank_workbook provided
      - no_leave: sorted list of employee ids with no leave rows
      - warnings: combined parse warnings
      - bank_uploaded: whether a bank workbook was supplied
    """
    allowed = frozenset(allowed_codes) if allowed_codes is not None else DEFAULT_ALLOWED_CODES

    roster_ids, roster_warnings = parse_roster(roster_text)
    hours_rows, hours_warnings = parse_hours_csv(hours_text)
    warnings = roster_warnings + hours_warnings

    leave_by_emp: dict[str, list[dict]] = {}

    for hr in hours_rows:
        if hr.employee_id not in roster_ids:
            continue
        if hr.pay_code in allowed:
            continue
        row_dict = {
            "hours": hr.hours,
            "pay_code": hr.pay_code,
            "date": hr.date,
            "line_number": hr.line_number,
            "_sort": _parse_sort_date(hr.date),
        }
        leave_by_emp.setdefault(hr.employee_id, []).append(row_dict)

    for eid, lst in leave_by_emp.items():
        lst.sort(key=lambda r: (r["_sort"], r["line_number"]))
        for r in lst:
            del r["_sort"]

    used_ids = sorted(leave_by_emp.keys(), key=int)
    used_leave: list[dict] = []
    for eid in used_ids:
        rows = leave_by_emp[eid]
        total = 0.0
        for r in rows:
            h = _parse_hours_value(r["hours"])
            if h is not None:
                total += h
            else:
                warnings.append(
                    f"Hours line {r['line_number']}: could not parse hours for total: {r['hours']!r}"
                )
        used_leave.append(
            {
                "employee_id": eid,
                "rows": rows,
                "total_hours": f"{total:.2f}",
            }
        )

    if bank_workbook is not None:
        banks_map, bank_warnings = parse_leavebank_workbook(bank_workbook)
        warnings.extend(bank_warnings)
        for item in used_leave:
            eid = item["employee_id"]
            if eid in banks_map:
                item["bank"] = {"found": True, "columns": banks_map[eid]}
            else:
                item["bank"] = {
                    "found": False,
                    "message": "No row for this employee in the bank file.",
                }

    for item in used_leave:
        ledger = aggregate_ledger_for_leave_rows(item["rows"], item["employee_id"])
        warnings.extend(ledger["unmapped_notes"])
        truth_cols = None
        b = item.get("bank")
        if b and b.get("found"):
            truth_cols = b["columns"]
        item["ledger"] = {
            "unpaid_hours_str": ledger["unpaid_hours_str"],
            "debits": ledger["debits"],
            "unmapped_notes": ledger["unmapped_notes"],
        }
        item["has_unmapped"] = len(ledger["unmapped_notes"]) > 0
        item["period_debits"] = {
            "columns": build_period_debit_columns(ledger["debits"], truth_cols),
        }
        if truth_cols:
            recon_rows = build_reconciliation_rows(ledger["debits"], truth_cols)
            item["reconciliation"] = {"rows": recon_rows}
            banks_short = [r["label"] for r in recon_rows if r.get("status") == "short"]
            item["has_short"] = len(banks_short) > 0
            item["banks_short"] = banks_short
            chips: list[dict[str, str]] = []
            seen_keys: set[str] = set()
            for r in recon_rows:
                if r.get("status") != "short":
                    continue
                k = r.get("key") or ""
                if not k or k in seen_keys:
                    continue
                seen_keys.add(k)
                chips.append({"key": k, "label": r.get("label", k)})
            item["banks_short_chips"] = chips
            item["banks_short_keys"] = [c["key"] for c in chips]
        else:
            item["reconciliation"] = None
            item["has_short"] = False
            item["banks_short"] = []
            item["banks_short_chips"] = []
            item["banks_short_keys"] = []

    short_employees: list[dict] = []
    unmapped_employee_count = 0
    for item in used_leave:
        if item.get("has_unmapped"):
            unmapped_employee_count += 1
        if item.get("has_short"):
            short_employees.append(
                {
                    "employee_id": item["employee_id"],
                    "banks_short": item.get("banks_short", []),
                    "banks_short_keys": item.get("banks_short_keys", []),
                    "banks_short_chips": item.get("banks_short_chips", []),
                }
            )
    short_employees.sort(key=lambda x: int(x["employee_id"]))

    no_leave = sorted(roster_ids - set(leave_by_emp.keys()), key=int)

    return {
        "roster_ids": sorted(roster_ids, key=int),
        "roster_count": len(roster_ids),
        "used_leave": used_leave,
        "no_leave": no_leave,
        "warnings": warnings,
        "allowed_codes": sorted(allowed),
        "bank_uploaded": bank_workbook is not None,
        "summary": {
            "short_count": len(short_employees),
            "short_employees": short_employees,
            "unmapped_employee_count": unmapped_employee_count,
        },
    }

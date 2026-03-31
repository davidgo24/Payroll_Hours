"""Sunday–Saturday week view: reg vs OT totals and cohorts (balance OK / no leave)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from leave_logic import (
    DEFAULT_ALLOWED_CODES,
    HoursRow,
    _parse_hours_value,
    parse_hours_csv,
    parse_roster,
)
from leavebank_io import parse_leavebank_workbook
from pay_code_mapping import aggregate_ledger_for_leave_rows, build_reconciliation_rows

OT_PAY_CODES = frozenset({"OT 1.5", "OT 1.0", "CT EARN 1.5", "CT EARN 1.0"})

DAY_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def normalize_pay_code(raw: str) -> str:
    return raw.strip().upper()


def parse_row_date(date_str: str) -> date | None:
    """Parse date from hours CSV; supports M/D/Y and ISO YYYY-MM-DD (common exports)."""
    raw = date_str.strip()
    if not raw:
        return None
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def sunday_of_week_containing(d: date) -> date:
    """Week runs Sunday–Saturday; `d` may be any day in that week."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def parse_iso_week_anchor(s: str) -> date | None:
    """Accept YYYY-MM-DD from HTML date input."""
    s = s.strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _leave_row_dicts_from_hours(rows: list[HoursRow], allowed: frozenset[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hr in rows:
        if hr.pay_code in allowed:
            continue
        out.append(
            {
                "hours": hr.hours,
                "pay_code": hr.pay_code,
                "date": hr.date,
                "line_number": hr.line_number,
            }
        )
    return out


def _reconcile_leave_week(
    eid: str,
    leave_rows: list[dict[str, Any]],
    banks_by_emp: dict[str, dict[str, Any]],
    bank_workbook_present: bool,
) -> tuple[bool, bool]:
    """Returns (has_unmapped, has_short)."""
    ledger = aggregate_ledger_for_leave_rows(leave_rows, eid)
    has_unmapped = len(ledger["unmapped_notes"]) > 0
    if not bank_workbook_present:
        return has_unmapped, False
    b = banks_by_emp.get(eid)
    truth_cols = None
    if b and b.get("found"):
        truth_cols = b["columns"]
    if not truth_cols:
        return has_unmapped, False
    recon_rows = build_reconciliation_rows(ledger["debits"], truth_cols)
    has_short = any(r.get("status") == "short" for r in recon_rows)
    return has_unmapped, has_short


def _build_employee_block(
    eid: str,
    rows: list[HoursRow],
    week_start: date,
) -> dict[str, Any]:
    total = reg = ot = 0.0
    day_totals = [0.0] * 7
    line_rows: list[dict[str, Any]] = []

    for hr in rows:
        h = _parse_hours_value(hr.hours)
        if h is None:
            h = 0.0
        total += h
        dt = parse_row_date(hr.date)
        if dt is not None and week_start <= dt <= week_start + timedelta(days=6):
            idx = (dt - week_start).days
            if 0 <= idx <= 6:
                day_totals[idx] += h

        ncode = normalize_pay_code(hr.pay_code)
        if ncode in OT_PAY_CODES:
            ot += h
            bucket = "ot"
        else:
            reg += h
            bucket = "reg"
        line_rows.append(
            {
                "date": hr.date,
                "hours": hr.hours,
                "pay_code": hr.pay_code,
                "line_number": hr.line_number,
                "bucket": bucket,
            }
        )

    day_strip = []
    for i, lbl in enumerate(DAY_LABELS):
        hrs = day_totals[i]
        day_strip.append(
            {
                "label": lbl,
                "hours_str": f"{hrs:.2f}" if hrs > 1e-9 else "",
                "has_hours": hrs > 1e-9,
            }
        )

    return {
        "employee_id": eid,
        "line_rows": line_rows,
        "total_hours": f"{total:.2f}",
        "reg_hours": f"{reg:.2f}",
        "ot_hours": f"{ot:.2f}",
        "day_strip": day_strip,
    }


def analyze_week(
    hours_text: str,
    roster_text: str,
    week_anchor: date,
    allowed_codes: Iterable[str] | None = None,
    bank_workbook: bytes | None = None,
) -> dict[str, Any]:
    """
    week_anchor: any calendar day; week is normalized to Sunday–Saturday containing it.

    Returns keys: week_start, week_end, week_label, bank_uploaded, bank_verifies_short,
      warnings, allowed_codes,
      section_balance_ok, section_no_leave, excluded_note, omitted_short, omitted_unmapped.
    """
    allowed = frozenset(allowed_codes) if allowed_codes is not None else DEFAULT_ALLOWED_CODES
    roster_ids, roster_warnings = parse_roster(roster_text)
    hours_rows, hours_warnings = parse_hours_csv(hours_text)
    warnings = list(roster_warnings) + list(hours_warnings)

    week_start = sunday_of_week_containing(week_anchor)
    week_end = week_start + timedelta(days=6)

    by_emp: dict[str, list[HoursRow]] = {}
    for hr in hours_rows:
        if hr.employee_id not in roster_ids:
            continue
        dt = parse_row_date(hr.date)
        if dt is None:
            warnings.append(
                f"Hours line {hr.line_number}: could not parse date {hr.date!r} (expected M/D/YYYY); row skipped for weekly view."
            )
            continue
        if not (week_start <= dt <= week_end):
            continue
        by_emp.setdefault(hr.employee_id, []).append(hr)

    for eid in by_emp:
        by_emp[eid].sort(key=lambda r: (parse_row_date(r.date) or date.min, r.line_number))

    banks_by_emp: dict[str, dict[str, Any]] = {}
    bank_uploaded = bank_workbook is not None
    if bank_workbook is not None:
        banks_map, bank_warnings = parse_leavebank_workbook(bank_workbook)
        warnings.extend(bank_warnings)
        for eid, cols in banks_map.items():
            banks_by_emp[eid] = {"found": True, "columns": cols}

    section_balance_ok: list[dict[str, Any]] = []
    section_no_leave: list[dict[str, Any]] = []
    omitted_short = 0
    omitted_unmapped = 0

    for eid in sorted(roster_ids, key=int):
        rows = by_emp.get(eid, [])
        leave_rows = _leave_row_dicts_from_hours(rows, allowed)
        block = _build_employee_block(eid, rows, week_start)

        if not leave_rows:
            section_no_leave.append(block)
            continue

        has_unmapped, has_short = _reconcile_leave_week(eid, leave_rows, banks_by_emp, bank_uploaded)

        if has_unmapped:
            omitted_unmapped += 1
            continue
        if has_short:
            omitted_short += 1
            continue

        section_balance_ok.append(block)

    parts = []
    if omitted_short:
        parts.append(f"{omitted_short} with a short bank balance")
    if omitted_unmapped:
        parts.append(f"{omitted_unmapped} with unmapped pay codes")
    excluded_note = "; ".join(parts) if parts else ""

    matched_week_row_count = sum(len(v) for v in by_emp.values())

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_label": f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}",
        "bank_uploaded": bank_uploaded,
        "bank_verifies_short": bank_uploaded,
        "warnings": warnings,
        "allowed_codes": sorted(allowed),
        "section_balance_ok": section_balance_ok,
        "section_no_leave": section_no_leave,
        "excluded_note": excluded_note,
        "omitted_short": omitted_short,
        "omitted_unmapped": omitted_unmapped,
        "total_hours_rows": len(hours_rows),
        "matched_week_row_count": matched_week_row_count,
    }

"""Pay code → accrual bank key; unpaid vs mapped leave rows (used-leave rows only)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# Codes that never draw from an accrual bank (unpaid).
UNPAID_CODES: frozenset[str] = frozenset(
    {
        "LWOP",
        "FMLA LWOP",
    }
)

# Exact pay code (after normalization) → bank column key (must match leavebank_io keys).
PAY_CODE_TO_BANK: dict[str, str] = {
    "SICK PAY": "SICK",
    "SICK": "SICK",
    "VAC PAY": "VAC",
    "VAC": "VAC",
    "AL PAY": "AL",
    "ADMIN LEAVE PAY": "AL",
    "CT PAY 1.0": "COMP",
    "CT PAY": "COMP",
    "FMLA CT PAY": "COMP",
    "HOLIDAY": "HOLIDAY",
    "HOLIDAY PAY": "HOLIDAY",
}


def _parse_hours(s: str) -> float | None:
    try:
        return float(s.strip().replace(",", ""))
    except ValueError:
        return None


def classify_pay_code(raw: str) -> tuple[str, str | None]:
    """
    Classify a pay code for bank debiting.

    Returns:
      ("unpaid", None) — does not debit banks
      ("unmapped", None) — not in mapping
      ("mapped", bank_key) — debits this bank column
    """
    p = raw.strip().upper()
    if not p:
        return "unmapped", None

    if p.startswith("FMLA "):
        rest = p[5:].strip()
        if rest == "LWOP" or rest.endswith(" LWOP"):
            return "unpaid", None
        p = rest

    if p in UNPAID_CODES or p == "LWOP":
        return "unpaid", None

    bank = PAY_CODE_TO_BANK.get(p)
    if bank is None:
        return "unmapped", None
    return "mapped", bank


def aggregate_ledger_for_leave_rows(
    rows: list[dict[str, Any]],
    employee_id: str,
) -> dict[str, Any]:
    """
    Sum debits per bank key, unpaid hours, and collect unmapped warnings.

    Each row must have hours, pay_code, line_number.
    """
    debits: dict[str, float] = defaultdict(float)
    unpaid = 0.0
    unmapped_notes: list[str] = []

    for r in rows:
        pay_code = r.get("pay_code", "")
        h = _parse_hours(str(r.get("hours", "")))
        if h is None:
            continue
        kind, bank_key = classify_pay_code(pay_code)
        if kind == "unpaid":
            unpaid += h
        elif kind == "mapped" and bank_key:
            debits[bank_key] += h
        else:
            ln = r.get("line_number", "?")
            unmapped_notes.append(
                f"Employee {employee_id} hours line {ln}: unmapped pay code {pay_code!r}"
            )

    return {
        "debits": dict(debits),
        "unpaid_hours": unpaid,
        "unpaid_hours_str": f"{unpaid:.2f}",
        "unmapped_notes": unmapped_notes,
    }


def build_period_debit_columns(
    debits: dict[str, float],
    truth_columns: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """
    Rows for the period debits table: same order as truth bank when provided;
    otherwise alphabetical by bank key.
    """
    if truth_columns:
        out: list[dict[str, str]] = []
        for col in truth_columns:
            key = col["key"]
            d = debits.get(key, 0.0)
            out.append(
                {
                    "key": key,
                    "label": col.get("label", key),
                    "debit": f"{d:.2f}",
                }
            )
        for key, total in sorted(debits.items()):
            if any(c["key"] == key for c in truth_columns):
                continue
            out.append({"key": key, "label": key, "debit": f"{total:.2f}"})
        return out

    out = []
    for key in sorted(debits.keys()):
        total = debits[key]
        out.append({"key": key, "label": key, "debit": f"{total:.2f}"})
    return out


def build_reconciliation_rows(
    debits: dict[str, float],
    truth_columns: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Per bank key: balance, debit, remaining, status (ok|short|missing)."""
    if not truth_columns:
        return []

    rows: list[dict[str, str]] = []
    truth_by_key = {c["key"]: c for c in truth_columns}

    keys_in_order = [c["key"] for c in truth_columns]
    extra_keys = sorted(k for k in debits if k not in truth_by_key)

    for key in keys_in_order + extra_keys:
        debit = debits.get(key, 0.0)
        if key not in truth_by_key:
            rows.append(
                {
                    "key": key,
                    "label": key,
                    "balance": "—",
                    "debit": f"{debit:.2f}",
                    "remaining": "—",
                    "status": "missing",
                    "status_label": "No balance column",
                }
            )
            continue

        col = truth_by_key[key]
        label = col.get("label", key)
        bal_s = col.get("value", "0.00")
        try:
            balance = float(bal_s.replace(",", "").strip())
        except ValueError:
            balance = 0.0
        remaining = balance - debit
        if debit <= balance + 1e-6:
            st = "ok"
            st_label = "OK"
        else:
            st = "short"
            st_label = "Short"

        rows.append(
            {
                "key": key,
                "label": label,
                "balance": f"{balance:.2f}",
                "debit": f"{debit:.2f}",
                "remaining": f"{remaining:.2f}",
                "status": st,
                "status_label": st_label,
            }
        )

    return rows

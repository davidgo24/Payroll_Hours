"""FastAPI app: upload hours CSV + roster, show leave buckets."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from leave_logic import DEFAULT_ALLOWED_CODES, analyze
from weekly_hours import analyze_week, parse_iso_week_anchor, sunday_of_week_containing

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Leave buckets", version="0.1.0")
# Railway (and other reverse proxies) send X-Forwarded-*; trust them for correct URLs/scheme.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _decode_upload(data: bytes) -> str:
    return data.decode("utf-8-sig")


def _allowed_from_env():
    raw = os.environ.get("ALLOWED_CODES", "").strip()
    if not raw:
        return None
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    return parts if parts else None


def _display_allowed():
    env = _allowed_from_env()
    return sorted(env) if env else sorted(DEFAULT_ALLOWED_CODES)


def _default_week_iso() -> str:
    return sunday_of_week_containing(date.today()).isoformat()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": None,
            "result": None,
            "allowed_codes": _display_allowed(),
        },
    )


@app.post("/", response_class=HTMLResponse)
async def analyze_upload(
    request: Request,
    hours: UploadFile | None = File(None),
    roster: UploadFile | None = File(None),
    bank: UploadFile | None = File(None),
):
    allowed = _allowed_from_env()

    if hours is None or not hours.filename:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Please upload a hours CSV file.",
                "result": None,
                "allowed_codes": _display_allowed(),
            },
            status_code=400,
        )
    if roster is None or not roster.filename:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Please upload an employee roster file.",
                "result": None,
                "allowed_codes": _display_allowed(),
            },
            status_code=400,
        )

    hours_bytes = await hours.read()
    roster_bytes = await roster.read()

    bank_bytes: bytes | None = None
    extra_warnings: list[str] = []
    if bank is not None and bank.filename:
        if bank.filename.lower().endswith(".xlsx"):
            bank_bytes = await bank.read()
        else:
            extra_warnings.append("Bank file was not a .xlsx; ignored.")

    try:
        hours_text = _decode_upload(hours_bytes)
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Hours file must be UTF-8 text.",
                "result": None,
                "allowed_codes": _display_allowed(),
            },
            status_code=400,
        )

    try:
        roster_text = _decode_upload(roster_bytes)
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Roster file must be UTF-8 text.",
                "result": None,
                "allowed_codes": _display_allowed(),
            },
            status_code=400,
        )

    result = analyze(
        hours_text,
        roster_text,
        allowed_codes=allowed,
        bank_workbook=bank_bytes,
    )
    if extra_warnings:
        result["warnings"] = extra_warnings + result["warnings"]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": None,
            "result": result,
            "allowed_codes": result["allowed_codes"],
        },
    )


@app.get("/weekly-hours", response_class=HTMLResponse)
async def weekly_hours_get(request: Request):
    return templates.TemplateResponse(
        "weekly.html",
        {
            "request": request,
            "error": None,
            "result": None,
            "allowed_codes": _display_allowed(),
            "default_week": _default_week_iso(),
        },
    )


@app.post("/weekly-hours", response_class=HTMLResponse)
async def weekly_hours_post(
    request: Request,
    hours: UploadFile | None = File(None),
    roster: UploadFile | None = File(None),
    bank: UploadFile | None = File(None),
    week_start: str = Form(""),
):
    allowed = _allowed_from_env()
    week_anchor = parse_iso_week_anchor(week_start)
    if week_anchor is None:
        return templates.TemplateResponse(
            "weekly.html",
            {
                "request": request,
                "error": "Choose a valid week (date).",
                "result": None,
                "allowed_codes": _display_allowed(),
                "default_week": _default_week_iso(),
            },
            status_code=400,
        )

    if hours is None or not hours.filename:
        return templates.TemplateResponse(
            "weekly.html",
            {
                "request": request,
                "error": "Please upload a hours CSV file.",
                "result": None,
                "allowed_codes": _display_allowed(),
                "default_week": week_anchor.isoformat(),
            },
            status_code=400,
        )
    if roster is None or not roster.filename:
        return templates.TemplateResponse(
            "weekly.html",
            {
                "request": request,
                "error": "Please upload an employee roster file.",
                "result": None,
                "allowed_codes": _display_allowed(),
                "default_week": week_anchor.isoformat(),
            },
            status_code=400,
        )

    hours_bytes = await hours.read()
    roster_bytes = await roster.read()

    bank_bytes: bytes | None = None
    extra_warnings: list[str] = []
    if bank is not None and bank.filename:
        if bank.filename.lower().endswith(".xlsx"):
            bank_bytes = await bank.read()
        else:
            extra_warnings.append("Bank file was not a .xlsx; ignored.")

    try:
        hours_text = _decode_upload(hours_bytes)
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "weekly.html",
            {
                "request": request,
                "error": "Hours file must be UTF-8 text.",
                "result": None,
                "allowed_codes": _display_allowed(),
                "default_week": week_anchor.isoformat(),
            },
            status_code=400,
        )

    try:
        roster_text = _decode_upload(roster_bytes)
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "weekly.html",
            {
                "request": request,
                "error": "Roster file must be UTF-8 text.",
                "result": None,
                "allowed_codes": _display_allowed(),
                "default_week": week_anchor.isoformat(),
            },
            status_code=400,
        )

    result = analyze_week(
        hours_text,
        roster_text,
        week_anchor=week_anchor,
        allowed_codes=allowed,
        bank_workbook=bank_bytes,
    )
    if extra_warnings:
        result["warnings"] = extra_warnings + result["warnings"]

    return templates.TemplateResponse(
        "weekly.html",
        {
            "request": request,
            "error": None,
            "result": result,
            "allowed_codes": result["allowed_codes"],
            "default_week": result["week_start"],
        },
    )

"""Private, stateless API. Public access is through the authenticated web app."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from research.data.stock_model import build_stock_model
from research.providers.base import ProviderError
from research.providers.llm import ModelUnavailableError, ResearchModelClient
from research.providers.market_assumptions import fetch_india_market_assumptions
from research.providers.yahoo import (
    YahooFinanceProvider,
    YahooInvalidDataError,
    YahooRateLimitError,
)
from research.reporting.excel_model import build_stock_workbook
from research.reporting.live_report import render_live_report
from research.reporting.pdf_report import render_report_pdf
from services.api.app.core.config import Settings, get_settings

MAX_BODY = 384 * 1024
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
app = FastAPI(title="Astra private research API", docs_url=None, redoc_url=None, openapi_url=None)
pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="research")
slots = asyncio.Semaphore(3)


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    ticker: str = Field(
        min_length=1, max_length=40, pattern=r"^[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,39}$"
    )
    assumptions: dict[str, FiniteNumber] | None = Field(default=None, max_length=32)
    display_unit: Literal["auto", "crore", "lakh", "million", "absolute"] = "auto"
    refresh: bool = False
    snapshot_id: str | None = Field(default=None, min_length=80, max_length=220000)


class ReportRequest(ModelRequest):
    research_brief: str = Field(default="", max_length=4000)
    source_urls: list[Annotated[str, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=3
    )
    include_ai: bool = False


def configured_settings() -> Settings:
    try:
        settings = get_settings()
        if not settings.internal_api_token or len(settings.internal_api_token) < 32:
            raise ValueError("missing internal credential")
        Fernet((settings.snapshot_encryption_key or "").encode("ascii"))
        return settings
    except (ValueError, UnicodeError):
        raise HTTPException(
            503, "Server credentials are not configured. See deployment instructions."
        ) from None


class PrivateBoundary:
    """Authenticate before parsing a bounded request body; never trust forwarded client identity."""

    def __init__(self, app):
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        if scope["path"] != "/health":
            try:
                settings = configured_settings()
            except HTTPException as exc:
                return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(
                    scope, receive, send
                )
            expected = f"Bearer {settings.internal_api_token}".encode()
            owner = headers.get(b"x-workspace-owner", b"")
            if not hmac.compare_digest(
                headers.get(b"authorization", b""), expected
            ) or not re.fullmatch(rb"[a-f0-9]{64}", owner):
                return await JSONResponse({"detail": "Authentication required."}, status_code=401)(
                    scope, receive, send
                )
            scope.setdefault("state", {})["owner"] = owner.decode()
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            if not headers.get(b"content-type", b"").lower().startswith(b"application/json"):
                return await JSONResponse({"detail": "Send application/json."}, status_code=415)(
                    scope, receive, send
                )
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_BODY:
                    return await JSONResponse(
                        {"detail": "Request body is too large."}, status_code=413
                    )(scope, receive, send)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            receive_request = bounded_receive
        else:
            receive_request = receive

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store, private"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        return await self.inner(scope, receive_request, protected_send)


app.add_middleware(PrivateBoundary)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, exc):
    # Pydantic's normal response includes the rejected input, potentially a secret.
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse({"detail": errors}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_request, _exc):
    return JSONResponse(
        {"detail": "The research request could not be completed. Please retry."}, status_code=500
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/config")
def capabilities():
    settings = configured_settings()
    client = ResearchModelClient(settings)
    return {
        "app_mode": "LIVE",
        "live_ready": True,
        "model_provider": client.provider,
        "ai_available": client.available,
    }


def _cipher(settings: Settings) -> Fernet:
    return Fernet(settings.snapshot_encryption_key.encode("ascii"))


def _normal_ticker(value: str) -> str:
    value = value.upper()
    return value if "." in value else value + ".NS"


def requested_model(request: ModelRequest, settings: Settings, owner: str) -> dict:
    token = request.snapshot_id if not request.refresh else None
    if token:
        try:
            payload = json.loads(
                _cipher(settings).decrypt(token.encode("ascii"), ttl=settings.snapshot_ttl_seconds)
            )
            if payload.get("v") != 2 or not hmac.compare_digest(payload.get("owner", ""), owner):
                raise ValueError("owner")
            snapshot = payload["snapshot"]
            if snapshot["ticker"] != _normal_ticker(request.ticker):
                raise ValueError("ticker")
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            raise HTTPException(
                410,
                "These observations have expired or do not belong to this stock and session. Reload the stock.",
            ) from None
    else:
        snapshot = YahooFinanceProvider(settings).fetch_company(
            request.ticker, refresh=request.refresh
        )
        if snapshot.get("currency") == "INR" and not any(
            word in str(snapshot.get("sector") or "").lower()
            for word in ("financial", "bank", "insurance")
        ):
            market = fetch_india_market_assumptions(settings, refresh=request.refresh)
            snapshot["market_assumptions"] = market
            snapshot["warnings"] = [*snapshot.get("warnings", []), *market.get("warnings", [])]
            snapshot["citations"] = [*snapshot.get("citations", []), *market.get("citations", [])]
        payload = {"v": 2, "owner": owner, "snapshot": snapshot}
        token = (
            _cipher(settings)
            .encrypt(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
            .decode("ascii")
        )
    try:
        model = build_stock_model(snapshot, request.assumptions, request.display_unit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:300]) from None
    model["snapshot_id"] = token
    model["input_overrides"] = request.assumptions
    return model


async def _work(function, *args, timeout: float = 180):
    # Timed-out requests retain their worker slot until the underlying blocking call finishes.
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(
            429, "Research is busy. Retry shortly.", headers={"Retry-After": "15"}
        ) from None
    future = asyncio.get_running_loop().run_in_executor(pool, function, *args)
    future.add_done_callback(lambda _result: slots.release())
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except TimeoutError:
        raise HTTPException(
            504, "The data provider took too long. Retry shortly; no replacement data was used."
        ) from None
    except YahooRateLimitError:
        raise HTTPException(
            429,
            "Yahoo Finance is rate limiting requests. Retry later.",
            headers={"Retry-After": "60"},
        ) from None
    except YahooInvalidDataError as exc:
        raise HTTPException(422, str(exc)[:400]) from None
    except ProviderError:
        raise HTTPException(
            502,
            "Yahoo Finance could not supply usable observations for this ticker. Verify the listing and retry later.",
        ) from None
    except ValueError:
        raise HTTPException(
            422,
            "The request or source document could not be processed. Check the inputs and retry.",
        ) from None


def _filename(ticker: str, suffix: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", ticker.lower())[:45] + suffix


@app.post("/v1/stocks/model")
async def stock_model(request: ModelRequest, context: Request):
    settings = configured_settings()
    return await _work(
        requested_model,
        request,
        settings,
        context.state.owner,
        timeout=settings.stock_model_timeout_seconds,
    )


def _excel(request, settings, owner):
    model = requested_model(request, settings, owner)
    return build_stock_workbook(model), model["ticker"]


@app.post("/v1/stocks/model/export")
async def export_model(request: ModelRequest, context: Request):
    content, ticker = await _work(_excel, request, configured_settings(), context.state.owner)
    return Response(
        content,
        media_type=XLSX_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{_filename(ticker, "-dcf.xlsx")}"'},
    )


def _research_context(model: dict, documents: list) -> dict:
    """Send a compact public evidence set; keep valuation out of truncated source blobs."""
    source = model.get("source_snapshot") or {}
    history_keys = (
        "year",
        "period",
        "revenue",
        "ebit",
        "operating_cash_flow",
        "capital_expenditure",
        "total_debt",
        "cash_and_equivalents",
    )
    notes = model.get("assumption_notes") or {}
    return {
        "metrics": {
            "valuation_date": model.get("valuation_date"),
            "base_period": model.get("base_period"),
            "currency": model.get("currency"),
            "money_basis": "All financial numbers below retain absolute reporting-currency units; prices are per share.",
            "historical": [
                {key: row.get(key) for key in history_keys}
                for row in model.get("historical", [])[-3:]
            ],
            "terminal": model.get("terminal"),
            "wacc": model.get("wacc"),
            "quality": model.get("quality"),
        },
        "scenario_valuation": model.get("valuation"),
        "assumptions": {
            "inputs": model.get("assumptions"),
            "basis": {
                key: str(notes.get(key, ""))[:230]
                for key in (
                    "revenue_growth",
                    "ebit_margin",
                    "capex_rate",
                    "risk_free_rate",
                    "equity_risk_premium",
                    "beta",
                    "terminal_roic",
                )
            },
        },
        "company": {
            "name": model.get("company"),
            "ticker": model.get("ticker"),
            "sector": source.get("sector"),
            "description": str(source.get("business_summary") or "")[:1400],
            "financial_basis": source.get("financial_basis"),
        },
        "citations": source.get("citations", []),
        "warnings": model.get("warnings", [])[:12],
        "documents": [{**doc, "source_url": doc.get("url")} for doc in documents],
    }


def _pdf(request: ReportRequest, settings: Settings, owner: str):
    from services.serverless.sources import fetch_official_sources

    model = requested_model(request, settings, owner)
    state = {
        "stock_model": model,
        "research_brief": request.research_brief,
        "prompt": request.research_brief,
        "display_unit": request.display_unit,
    }
    snapshot = model.get("source_snapshot", {})
    documents = (
        fetch_official_sources(request.source_urls, snapshot.get("website"))
        if request.source_urls
        else []
    )
    state["source_contexts"] = [
        {**doc, "source_version_id": f"public-document-{index + 1}"}
        for index, doc in enumerate(documents)
    ]
    state["citations"] = [
        *snapshot.get("citations", []),
        *[
            {
                "title": doc.get("title"),
                "uri": doc.get("url"),
                "provider": "company / exchange disclosure",
                "retrieved_label": snapshot.get("retrieved_at"),
            }
            for doc in documents
            if doc.get("content")
        ],
    ]
    state["warnings"] = [doc["warning"] for doc in documents if doc.get("warning")]
    if request.include_ai:
        client = ResearchModelClient(settings)
        if not client.available:
            raise HTTPException(
                422, "Optional AI narrative is not configured. Download a data-only report instead."
            )
        try:
            state["analyst_note"] = {
                "provider": client.provider,
                "text": client.generate(
                    "Equity research analyst",
                    request.research_brief
                    or "Assess the company's operating performance, valuation assumptions, and material research gaps.",
                    _research_context(model, documents),
                    qualitative_only=True,
                ),
            }
        except ModelUnavailableError as exc:
            state["warnings"].append(
                str(exc) + " This report contains the sourced data and deterministic analysis only."
            )
    return render_report_pdf(render_live_report(state)), model["ticker"]


@app.post("/v1/equity/report")
async def equity_report(request: ReportRequest, context: Request):
    content, ticker = await _work(_pdf, request, configured_settings(), context.state.owner)
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(ticker, "-equity-research.pdf")}"'
        },
    )

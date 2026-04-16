from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd
import pdfplumber
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langchain_community.tools import DuckDuckGoSearchResults
except ImportError:  # pragma: no cover
    tool = None
    ChatOpenAI = None
    DuckDuckGoSearchResults = None


CANONICAL_COLUMNS = {
    "date": {
        "date",
        "transaction date",
        "booking date",
        "value date",
        "posting date",
        "posted date",
        "post date",
        "trans date",
        "txn date",
        "movement date",
        "oper date",
        "operation date",
        "eff date",
        "effective date",
        "processing date",
        "trade date",
        "settlement date",
        "dt",
        "datum",
        "fecha",
        "valuta",
        "booked on",
        "day",
    },
    "description": {
        "description",
        "details",
        "merchant",
        "narrative",
        "particulars",
        "memo",
        "payee",
        "counterparty",
        "beneficiary",
        "narration",
        "subject",
        "booking text",
        "transaction details",
        "text",
        "type",
        "reference",
        "note",
        "notes",
    },
    "debit": {
        "debit",
        "withdrawal",
        "outflow",
        "money out",
        "expense",
        "debits",
        "paid out",
        "paid out amount",
        "charge",
    },
    "credit": {
        "credit",
        "deposit",
        "inflow",
        "money in",
        "income",
        "credits",
        "paid in",
        "received",
    },
    "amount": {"amount", "transaction amount", "amt", "value", "sum"},
    "balance": {"balance", "running balance", "closing balance", "account balance"},
}


@dataclass
class SessionData:
    frame: pd.DataFrame
    source_text: str
    currency: str
    analytics: dict


SESSIONS: dict[str, SessionData] = {}


class ChatRequest(BaseModel):
    question: str
    sessionId: str | None = None


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


def _to_float(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _canonicalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    renames: dict[str, str] = {}
    description_loosely_assigned = False
    for col in df.columns:
        normalized = _norm_col(str(col))
        matched = False
        for canonical, aliases in CANONICAL_COLUMNS.items():
            if normalized == canonical or normalized in aliases:
                renames[col] = canonical
                matched = True
                break
        if not matched:
            if "date" in normalized and canonical_date_like(normalized):
                renames[col] = "date"
            elif not description_loosely_assigned and any(
                k in normalized for k in ("memo", "narrative", "payee", "details", "description")
            ):
                renames[col] = "description"
                description_loosely_assigned = True
    return df.rename(columns=renames)


def canonical_date_like(normalized: str) -> bool:
    """True if normalized header is plausibly a transaction date column name."""
    skip = {"created date", "updated date", "import date", "export date", "statement date"}
    if normalized in skip:
        return False
    return True


def _infer_date_column(df: pd.DataFrame) -> pd.DataFrame | None:
    """If no 'date' column, pick the column whose values parse best as datetimes."""
    if "date" in df.columns:
        return df
    best_col: str | None = None
    best_score = 0.0
    for col in df.columns:
        if str(col).strip() == "":
            continue
        ser = df[col].dropna().head(100)
        if len(ser) < 3:
            continue
        parsed = pd.to_datetime(ser.astype(str), format="mixed", errors="coerce")
        score = float(parsed.notna().mean())
        if score > best_score:
            best_score = score
            best_col = str(col)
    if best_col is not None and best_score >= 0.4:
        out = df.rename(columns={best_col: "date"})
        return out
    return None


def _infer_description_column(df: pd.DataFrame) -> pd.DataFrame:
    """If no description, use first text-heavy column that is not the date."""
    if "description" in df.columns:
        return df
    skip_norm = {"amount", "debit", "credit", "balance", "date"}
    for col in df.columns:
        if str(col) == "date":
            continue
        if _norm_col(str(col)) in skip_norm:
            continue
        ser = df[col].dropna().astype(str).head(50)
        if ser.empty:
            continue
        avg_len = ser.str.len().mean()
        if avg_len >= 8 and df[col].dtype == object:
            return df.rename(columns={col: "description"})
    return df


def _collapse_duplicate_columns(df: pd.DataFrame, preferred: str) -> pd.Series:
    """Coalesce duplicate-named columns into one (left-most non-empty value wins)."""
    selected = df.loc[:, df.columns == preferred]
    if selected.empty:
        return pd.Series(dtype="object")
    if selected.shape[1] == 1:
        return selected.iloc[:, 0]
    out = selected.iloc[:, 0].copy()
    for i in range(1, selected.shape[1]):
        out = out.where(out.astype(str).str.strip() != "", selected.iloc[:, i])
        out = out.where(out.notna(), selected.iloc[:, i])
    return out


def _dedupe_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prevent duplicate canonical columns (e.g., two 'date' columns) after renaming.
    This avoids pandas datetime assembly errors like 'cannot assemble with duplicate keys'.
    """
    out = df.copy()
    for name in ("date", "description", "debit", "credit", "amount", "balance"):
        if (out.columns == name).sum() > 1:
            merged = _collapse_duplicate_columns(out, name)
            out = out.loc[:, out.columns != name]
            out[name] = merged
    return out


def _max_pdf_pages() -> int:
    """Cap pages processed to avoid OOM / very long requests on small hosts (e.g. Render free tier)."""
    try:
        return max(1, min(500, int(os.getenv("MAX_PDF_PAGES", "100"))))
    except ValueError:
        return 100


def _max_upload_bytes() -> int:
    try:
        return max(1_048_576, int(os.getenv("MAX_UPLOAD_BYTES", str(30 * 1024 * 1024))))
    except ValueError:
        return 30 * 1024 * 1024


def _row_cells(row: list) -> list[str]:
    return [str(c or "").strip() for c in row]


def _rows_equal_as_header(a: list[str], b: list[str]) -> bool:
    if len(a) != len(b) or not a:
        return False
    return all(_norm_col(str(x)) == _norm_col(str(y)) for x, y in zip(a, b))


def _table_fingerprint(tbl: list[list[str]]) -> tuple[str, ...]:
    if not tbl:
        return ()
    head = tuple(_row_cells(tbl[0])[:8])
    return (str(len(tbl)),) + head


def _extract_tables_from_pdf_page(page) -> list[list[list[str]]]:
    """Try default + alternate table settings (many bank PDFs need non-default strategies)."""
    tables: list[list[list[str]]] = []
    seen: set[tuple[str, ...]] = set()

    def add_table(raw: list[list] | None) -> None:
        if not raw or len(raw) < 2:
            return
        norm = [_row_cells(r) for r in raw]
        fp = _table_fingerprint(norm)
        if fp in seen:
            return
        seen.add(fp)
        tables.append(norm)

    add_table(page.extract_table())
    settings_opts = (
        {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
        {"vertical_strategy": "lines", "horizontal_strategy": "text", "intersection_tolerance": 8},
        {"vertical_strategy": "explicit", "horizontal_strategy": "explicit"},
    )
    for ts in settings_opts:
        try:
            for raw in page.extract_tables(table_settings=ts) or []:
                add_table(raw)
        except Exception:
            continue
    return tables


def _merge_flat_rows_from_tables(page_tables: list[list[list[str]]]) -> list[list[str]]:
    """Flatten tables into one row list; drop repeated header rows."""
    flat: list[list[str]] = []
    for tbl in page_tables:
        for r in tbl:
            flat.append(_row_cells(r))
    if not flat:
        return []
    header = flat[0]
    merged: list[list[str]] = [header]
    norm_header = [_norm_col(str(c)) for c in header]
    for r in flat[1:]:
        if not any(c for c in r):
            continue
        if len(r) == len(header) and all(_norm_col(str(a)) == b for a, b in zip(r, norm_header)):
            continue
        merged.append(r)
    return merged


def _load_pdf_tables_and_text(content: bytes) -> tuple[pd.DataFrame, str]:
    """Extract tables with several pdfplumber strategies; single PDF open."""
    max_pages = _max_pdf_pages()
    all_rows: list[list[str]] = []
    text_chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages[:max_pages]:
            text_chunks.append(page.extract_text() or "")
            page_tables = _extract_tables_from_pdf_page(page)
            if not page_tables:
                continue
            best_tbl = max(page_tables, key=len)
            chunk = _merge_flat_rows_from_tables([best_tbl])
            if len(chunk) > 1:
                if not all_rows:
                    all_rows.extend(chunk)
                else:
                    h0 = all_rows[0]
                    start = 1 if chunk and _rows_equal_as_header(chunk[0], h0) else 0
                    all_rows.extend(chunk[start:])
    if len(all_rows) < 2:
        hint = (
            f"No usable table in the first {max_pages} page(s). Tables may start later, or this may be a scanned/image PDF."
            if total > max_pages
            else "No usable table found in this PDF. Scanned or image-only statements need OCR, or try CSV/Excel export from your bank."
        )
        raise HTTPException(
            status_code=400,
            detail=f"{hint} You can set MAX_PDF_PAGES higher on the server or upload CSV/XLSX instead.",
        )
    header = all_rows[0]
    body = all_rows[1:]
    width = len(header)
    body = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in body]
    df = pd.DataFrame(body, columns=header)
    text = "\n".join(text_chunks).strip()
    return df, text


def _normalize_loaded_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().replace("\ufeff", "").strip() for c in out.columns]
    out = out.dropna(axis=1, how="all")
    return out


def _promote_best_header_row(df: pd.DataFrame, scan_rows: int = 10) -> pd.DataFrame:
    """Auto-detect and promote a likely header row from the first `scan_rows` rows."""
    if df.empty:
        return df

    n = min(scan_rows, len(df))
    header_terms = {
        "date",
        "transaction date",
        "value date",
        "booking date",
        "description",
        "details",
        "debit",
        "credit",
        "amount",
        "balance",
        "memo",
        "payee",
        "narrative",
    }

    best_idx = 0
    best_score = -1.0
    width = len(df.columns)

    for i in range(n):
        row = [str(v or "").strip() for v in df.iloc[i].tolist()]
        non_empty = [c for c in row if c]
        if not non_empty:
            continue
        norm = [_norm_col(c) for c in non_empty]
        keyword_hits = sum(1 for c in norm if c in header_terms or "date" in c)
        uniqueness = len(set(norm)) / max(1, len(non_empty))
        fill_ratio = len(non_empty) / max(1, width)
        score = (keyword_hits * 2.0) + uniqueness + fill_ratio
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx == 0:
        return df

    header = [str(v or "").strip() for v in df.iloc[best_idx].tolist()]
    if not any(header):
        return df

    dedup_count: dict[str, int] = {}
    normalized_header: list[str] = []
    for h in header:
        base = h if h else "column"
        count = dedup_count.get(base, 0)
        dedup_count[base] = count + 1
        normalized_header.append(base if count == 0 else f"{base}_{count + 1}")

    out = df.iloc[best_idx + 1 :].copy()
    out.columns = normalized_header
    return out.reset_index(drop=True)


def _read_csv_with_bad_lines(buf: io.StringIO, **kwargs: object) -> pd.DataFrame:
    try:
        return pd.read_csv(buf, on_bad_lines="skip", **kwargs)  # type: ignore[arg-type]
    except TypeError:
        buf.seek(0)
        return pd.read_csv(buf, **kwargs)  # type: ignore[arg-type]


def _read_csv_flexible(content: bytes) -> tuple[pd.DataFrame, str]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = content.decode("utf-8", errors="replace")

    last_err: str | None = None
    for read_fn in (
        lambda: _read_csv_with_bad_lines(io.StringIO(text), sep=None, engine="python"),
        lambda: _read_csv_with_bad_lines(io.StringIO(text), sep=";"),
        lambda: _read_csv_with_bad_lines(io.StringIO(text), sep=","),
        lambda: _read_csv_with_bad_lines(io.StringIO(text), sep="\t"),
    ):
        try:
            df = read_fn()
            df = _normalize_loaded_frame(df)
            if df.shape[1] >= 2:
                return df, text
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Could not parse CSV (need at least 2 columns). Try UTF-8 with comma or semicolon. ({last_err or 'unknown'})",
    )


def _read_excel_flexible(content: bytes) -> tuple[pd.DataFrame, str]:
    last_err: str | None = None
    for header_row in (0, 1, 2):
        try:
            df = pd.read_excel(io.BytesIO(content), header=header_row)
            df = _normalize_loaded_frame(df)
            if df.shape[1] >= 2 and df.shape[0] >= 1:
                return df, df.to_csv(index=False)
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Could not read Excel file. {last_err or 'Unknown error'}",
    )


def _load_dataframe(file: UploadFile, content: bytes) -> tuple[pd.DataFrame, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".csv":
        return _read_csv_flexible(content)
    if suffix in {".xlsx", ".xls"}:
        return _read_excel_flexible(content)
    if suffix == ".pdf":
        return _load_pdf_tables_and_text(content)
    raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, CSV, or Excel.")


def _prepare_financial_df(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows.")

    df = _normalize_loaded_frame(df.copy())
    df = _promote_best_header_row(df, scan_rows=10)
    df = _canonicalise_columns(df)
    df = _dedupe_canonical_columns(df)
    inferred = _infer_date_column(df)
    if inferred is not None:
        df = inferred
    if "date" not in df.columns:
        cols_preview = ", ".join(str(c) for c in list(df.columns)[:12])
        if len(df.columns) > 12:
            cols_preview += ", …"
        raise HTTPException(
            status_code=400,
            detail=(
                "No date column found in the uploaded document. "
                f"Columns detected: {cols_preview}. "
                "Rename a column to include 'Date' or export CSV with a clear date column."
            ),
        )

    df = _infer_description_column(df)
    if "description" not in df.columns:
        df["description"] = ""

    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    df = df[df["date"].notna()].copy()
    if df.empty:
        raise HTTPException(status_code=400, detail="No valid date rows after parsing.")

    for column in ("debit", "credit", "amount", "balance"):
        if column in df.columns:
            df[column] = _to_float(df[column])

    expense = pd.Series(0.0, index=df.index)
    if "debit" in df.columns:
        expense = df["debit"].fillna(0).clip(lower=0)
    if "amount" in df.columns:
        outgoing = df["amount"].where(df["amount"] < 0, other=0).abs()
        expense = expense.where(expense > 0, outgoing)
    if "balance" in df.columns:
        bal_drop = (df["balance"].shift(1) - df["balance"]).clip(lower=0).fillna(0)
        expense = expense.where(expense > 0, bal_drop)

    income = pd.Series(0.0, index=df.index)
    if "credit" in df.columns:
        income = df["credit"].fillna(0).clip(lower=0)
    if "amount" in df.columns:
        incoming = df["amount"].where(df["amount"] > 0, other=0)
        income = income.where(income > 0, incoming)

    df["expense"] = pd.to_numeric(expense, errors="coerce").fillna(0)
    df["income"] = pd.to_numeric(income, errors="coerce").fillna(0)
    df["description"] = df["description"].astype(str).fillna("")
    return df, "EUR"


def _merchant_like_key(text: str) -> str:
    lowered = re.sub(r"\s+", " ", str(text).lower()).strip()
    marker = re.search(r"(?:to|from):\s*([^,\n|]+)", lowered)
    if marker:
        cleaned = re.sub(r"[^a-z0-9\s&'-]", "", marker.group(1)).strip()
        return cleaned.title() if cleaned else "Other"
    lowered = re.sub(
        r"\b(reference|card|iban|pending|transfer|payment|cash|withdrawal)\b",
        " ",
        lowered,
    )
    lowered = re.sub(r"[^a-z\s]", " ", lowered)
    tokens = [t for t in lowered.split() if len(t) > 2]
    if not tokens:
        return "Other"
    return " ".join(tokens[:2]).title()


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / abs(previous)) * 100


def _build_analytics(df: pd.DataFrame, currency: str) -> dict:
    work = df.copy()
    work["month"] = work["date"].dt.to_period("M").astype(str)

    monthly_spending = work.groupby("month")["expense"].sum().sort_index()
    monthly_income = work.groupby("month")["income"].sum().sort_index()

    total_income = float(work["income"].sum())
    total_expenses = float(work["expense"].sum())
    net_balance = total_income - total_expenses

    current_month = monthly_spending.index[-1] if len(monthly_spending) else None
    prev_month = monthly_spending.index[-2] if len(monthly_spending) > 1 else None

    current_income = float(monthly_income.loc[current_month]) if current_month else total_income
    prev_income = float(monthly_income.loc[prev_month]) if prev_month else 0.0

    current_expense = float(monthly_spending.loc[current_month]) if current_month else total_expenses
    prev_expense = float(monthly_spending.loc[prev_month]) if prev_month else 0.0

    current_net = current_income - current_expense
    prev_net = prev_income - prev_expense

    work["category"] = work["description"].apply(_merchant_like_key)
    category_series = work.groupby("category")["expense"].sum().sort_values(ascending=False)
    total = float(category_series.sum())
    major_threshold = 0.03 * total
    major = category_series[category_series >= major_threshold]
    other_total = float(category_series[category_series < major_threshold].sum())
    if other_total > 0:
        major.loc["Other"] = major.get("Other", 0) + other_total

    return {
        "currency": currency,
        "summary": {
            "totalIncome": total_income,
            "totalExpenses": total_expenses,
            "netBalance": net_balance,
            "incomeChangePct": _pct_change(current_income, prev_income),
            "expenseChangePct": _pct_change(current_expense, prev_expense),
            "netBalanceChangePct": _pct_change(current_net, prev_net),
        },
        "spendingOverTime": [
            {"month": month, "amount": float(amount)} for month, amount in monthly_spending.items()
        ],
        "categoryBreakdown": [
            {"name": name, "value": float(value)} for name, value in major.items()
        ],
    }


def _fallback_chat(question: str, session: SessionData | None) -> tuple[str, str, str]:
    q = question.lower()
    if session and any(token in q for token in ("spend", "expense", "spent", "income", "balance", "transaction")):
        summary = session.analytics["summary"]
        currency = session.currency
        answer = (
            f"From your uploaded statement: total income is {currency} {summary['totalIncome']:,.2f}, "
            f"total expenses are {currency} {summary['totalExpenses']:,.2f}, "
            f"and net balance is {currency} {summary['netBalance']:,.2f}."
        )
        return answer, "document", "fallback_document_summary"
    return (
        "For general finance: track fixed vs variable costs, build a 3-6 month emergency fund, "
        "and automate monthly investing after essential expenses.",
        "general",
        "fallback_general_finance",
    )


class CalcPlan(BaseModel):
    operation: str = Field(default="sum_metric_on_date")
    metric: str = Field(default="expense")
    date: str | None = None
    start_date: str | None = None
    end_date: str | None = None


def _parse_date_safe(raw: str | None) -> pd.Timestamp | None:
    if not raw:
        return None
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt):
        return None
    return dt


def _split_text_chunks(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _retrieve_chunks(question: str, text: str, top_k: int = 4) -> list[str]:
    chunks = _split_text_chunks(text)
    if not chunks:
        return []
    terms = {w for w in re.findall(r"[a-zA-Z0-9]{3,}", question.lower())}
    if not terms:
        return chunks[:top_k]

    scored: list[tuple[int, str]] = []
    for ch in chunks:
        low = ch.lower()
        score = sum(1 for t in terms if t in low)
        scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = [c for s, c in scored[:top_k] if s > 0]
    return out if out else chunks[:top_k]


def _chat_with_model(question: str, session: SessionData | None) -> tuple[str, str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return _fallback_chat(question, session)

    # If LangChain or tooling deps are unavailable, keep the legacy OpenAI fallback path.
    if ChatOpenAI is None or tool is None:
        client = OpenAI(api_key=api_key)
        answer_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a practical personal finance advisor."},
                {"role": "user", "content": question},
            ],
            temperature=0.3,
        )
        return answer_resp.choices[0].message.content.strip(), "general", "legacy_general_llm"

    llm = ChatOpenAI(model="gpt-4o-mini", api_key=api_key, temperature=0)
    search_tool = None
    if DuckDuckGoSearchResults is not None:
        try:
            search_tool = DuckDuckGoSearchResults(max_results=5)
        except Exception:
            search_tool = None

    @tool("general_finance_timeless")
    def general_finance_timeless(question: str) -> str:
        """Use for timeless finance education and concepts that don't need current events."""
        resp = llm.invoke(
            "You are a concise finance educator. Answer timeless concepts clearly, "
            "with practical examples when useful. If a question needs up-to-date facts, say so briefly.\n\n"
            f"Question: {question}"
        )
        return str(resp.content).strip()

    @tool("general_finance_realtime_web")
    def general_finance_realtime_web(question: str) -> str:
        """Use for finance questions requiring current or recent information from the web."""
        search_results: str = ""
        if search_tool is not None:
            try:
                search_results = str(search_tool.invoke(question))
            except Exception as e:  # pragma: no cover
                search_results = f"(search failed: {e})"
        if not search_results:
            resp = llm.invoke(
                "You are a careful finance assistant. The server has no live web search available right now. "
                "Answer the question with caveats about potentially outdated information and suggest "
                "the user verify current data from an authoritative source.\n\n"
                f"Question: {question}"
            )
            return str(resp.content).strip()
        resp = llm.invoke(
            "You are a finance assistant using web results. Answer with current facts, "
            "mention uncertainty where relevant, and include source URLs inline when possible.\n\n"
            f"Question: {question}\n\nWeb results:\n{search_results}"
        )
        return str(resp.content).strip()

    @tool("document_finance_calculations")
    def document_finance_calculations(question: str) -> str:
        """Use for calculation-heavy questions about uploaded statement data (totals on dates, sums over ranges)."""
        if not session:
            return "Please upload a statement first so I can run calculations on your document."
        df = session.frame.copy()
        planner = llm.with_structured_output(CalcPlan)
        plan = planner.invoke(
            "Extract a calculation plan from the user question.\n"
            "Operations: sum_metric_on_date, total_metric, sum_metric_between_dates, avg_daily_metric.\n"
            "Metric must be one of expense,income,balance,amount.\n"
            "Return ISO dates (YYYY-MM-DD) when dates are provided.\n\n"
            f"Question: {question}"
        )
        metric = plan.metric if plan.metric in {"expense", "income", "balance", "amount"} else "expense"

        result_text = ""
        if plan.operation == "sum_metric_on_date":
            d = _parse_date_safe(plan.date)
            if d is not None:
                mask = df["date"].dt.date == d.date()
                total = float(df.loc[mask, metric].sum()) if metric in df.columns else 0.0
                result_text = f"{metric} on {d.date().isoformat()} = {session.currency} {total:,.2f}"
        elif plan.operation == "sum_metric_between_dates":
            d0 = _parse_date_safe(plan.start_date)
            d1 = _parse_date_safe(plan.end_date)
            if d0 is not None and d1 is not None and metric in df.columns:
                mask = (df["date"] >= d0) & (df["date"] <= d1)
                total = float(df.loc[mask, metric].sum())
                result_text = (
                    f"{metric} from {d0.date().isoformat()} to {d1.date().isoformat()} "
                    f"= {session.currency} {total:,.2f}"
                )
        elif plan.operation == "avg_daily_metric":
            if metric in df.columns and len(df):
                by_day = df.groupby(df["date"].dt.date)[metric].sum()
                avg = float(by_day.mean()) if len(by_day) else 0.0
                result_text = f"average daily {metric} = {session.currency} {avg:,.2f}"
        else:  # total_metric + unknown
            if metric in df.columns:
                total = float(df[metric].sum())
                result_text = f"total {metric} = {session.currency} {total:,.2f}"

        sample = df[["date", "description", "expense", "income"]].head(120).to_csv(index=False)
        resp = llm.invoke(
            "You are a financial assistant. Use the computed result and dataframe sample to answer. "
            "If computed_result is empty, explain what data is missing and suggest a precise question.\n\n"
            f"Question: {question}\nComputed_result: {result_text}\nData sample:\n{sample}"
        )
        return str(resp.content).strip()

    @tool("document_finance_non_calculation")
    def document_finance_non_calculation(question: str) -> str:
        """Use for non-calculation questions about the uploaded document using chunked document text context."""
        if not session:
            return "Please upload a statement first so I can answer document-specific questions."
        selected = _retrieve_chunks(question, session.source_text, top_k=4)
        context = "\n\n---\n\n".join(selected) if selected else "No extracted statement text found."
        resp = llm.invoke(
            "You answer using only the statement chunks. If unknown, say it's not present in the document.\n\n"
            f"Question: {question}\n\nStatement chunks:\n{context}"
        )
        return str(resp.content).strip()

    tools = [
        general_finance_timeless,
        general_finance_realtime_web,
        document_finance_calculations,
        document_finance_non_calculation,
    ]
    router = llm.bind_tools(tools, tool_choice="auto")
    routed = router.invoke(question)
    if not getattr(routed, "tool_calls", None):
        return general_finance_timeless.invoke({"question": question}), "general", "general_finance_timeless"

    call = routed.tool_calls[0]
    tool_name = str(call.get("name", ""))
    tool_args = call.get("args", {}) or {}
    tool_map: dict[str, tuple[Callable, str]] = {
        "general_finance_timeless": (general_finance_timeless, "general"),
        "general_finance_realtime_web": (general_finance_realtime_web, "general"),
        "document_finance_calculations": (document_finance_calculations, "document"),
        "document_finance_non_calculation": (document_finance_non_calculation, "document"),
    }
    selected_tool, route = tool_map.get(tool_name, (general_finance_timeless, "general"))
    result = selected_tool.invoke(tool_args if isinstance(tool_args, dict) else {"question": question})
    return str(result).strip(), route, (tool_name or "general_finance_timeless")


app = FastAPI(title="PocketWatch Backend")

# CORS: allow all origins (no credentials). CORS_ORIGINS env is not read here — wildcard + no paths.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Keep credentials disabled so wildcard CORS is valid in browsers.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/")
def root() -> dict[str, str]:
    """Avoid 404 on GET / (browsers, probes, Render health checks sometimes hit /)."""
    return {"service": "pocketwatch-backend", "health": "/api/health"}


@app.get("/health")
def health_alias() -> dict[str, str]:
    return health()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    max_bytes = _max_upload_bytes()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content)} bytes). Maximum is {max_bytes} bytes.",
        )
    df_raw, source_text = _load_dataframe(file, content)
    df, currency = _prepare_financial_df(df_raw)
    analytics = _build_analytics(df, currency)
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = SessionData(frame=df, source_text=source_text, currency=currency, analytics=analytics)
    return {"sessionId": session_id, **analytics}


@app.get("/api/analytics/{session_id}")
def analytics(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"sessionId": session_id, **session.analytics}


@app.post("/api/chat")
async def chat(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").lower()
    payload_data: dict = {}
    if "application/json" in content_type:
        try:
            payload_data = await request.json()
        except Exception:
            payload_data = {}
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        payload_data = dict(form)

    try:
        payload = ChatRequest(**payload_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chat payload. Provide 'question' and optional 'sessionId'.")

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    session = SESSIONS.get(payload.sessionId) if payload.sessionId else None
    try:
        answer, route, selected_tool = _chat_with_model(question, session)
    except Exception as e:  # pragma: no cover
        return {
            "answer": f"Chat failed on the server: {type(e).__name__}: {e}",
            "route": "general",
            "selectedTool": "error_fallback",
        }
    return {"answer": answer, "route": route, "selectedTool": selected_tool}


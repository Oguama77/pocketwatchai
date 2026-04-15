from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


CANONICAL_COLUMNS = {
    "date": {"date", "transaction date", "booking date", "value date"},
    "description": {"description", "details", "merchant", "narrative", "particulars"},
    "debit": {"debit", "withdrawal", "outflow", "money out", "expense"},
    "credit": {"credit", "deposit", "inflow", "money in", "income"},
    "amount": {"amount", "transaction amount"},
    "balance": {"balance", "running balance"},
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
    for col in df.columns:
        normalized = _norm_col(col)
        for canonical, aliases in CANONICAL_COLUMNS.items():
            if normalized == canonical or normalized in aliases:
                renames[col] = canonical
                break
    return df.rename(columns=renames)


def _extract_tables_from_pdf(content: bytes) -> pd.DataFrame:
    rows: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                rows.extend(table)
    if not rows:
        raise HTTPException(status_code=400, detail="Could not extract a table from this PDF.")
    header = [str(h or "").strip() for h in rows[0]]
    body = rows[1:]
    return pd.DataFrame(body, columns=header)


def _extract_text_from_pdf(content: bytes) -> str:
    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def _load_dataframe(file: UploadFile, content: bytes) -> tuple[pd.DataFrame, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".csv":
        text = content.decode("utf-8", errors="ignore")
        df = pd.read_csv(io.StringIO(text))
        return df, text
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(io.BytesIO(content))
        return df, df.to_csv(index=False)
    if suffix == ".pdf":
        df = _extract_tables_from_pdf(content)
        text = _extract_text_from_pdf(content)
        return df, text
    raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, CSV, or Excel.")


def _prepare_financial_df(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows.")

    df = _canonicalise_columns(df.copy())
    if "date" not in df.columns:
        raise HTTPException(status_code=400, detail="No date column found in uploaded data.")

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


def _fallback_chat(question: str, session: SessionData | None) -> tuple[str, str]:
    q = question.lower()
    if session and any(token in q for token in ("spend", "expense", "spent", "income", "balance", "transaction")):
        summary = session.analytics["summary"]
        currency = session.currency
        answer = (
            f"From your uploaded statement: total income is {currency} {summary['totalIncome']:,.2f}, "
            f"total expenses are {currency} {summary['totalExpenses']:,.2f}, "
            f"and net balance is {currency} {summary['netBalance']:,.2f}."
        )
        return answer, "document"
    return (
        "For general finance: track fixed vs variable costs, build a 3-6 month emergency fund, "
        "and automate monthly investing after essential expenses.",
        "general",
    )


def _chat_with_model(question: str, session: SessionData | None) -> tuple[str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return _fallback_chat(question, session)

    client = OpenAI(api_key=api_key)
    if session:
        context = session.frame[["date", "description", "expense", "income"]].head(120).to_csv(index=False)
    else:
        context = "No statement uploaded."

    router_prompt = (
        "Classify user question as 'document' (about their uploaded statement) or "
        "'general' (finance concepts). Return one word."
    )
    route_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": router_prompt},
            {"role": "user", "content": question},
        ],
        temperature=0,
    )
    route = route_resp.choices[0].message.content.strip().lower()
    route = "document" if "document" in route else "general"

    if route == "document" and session:
        system = (
            "You are a financial assistant. Answer using only the provided statement context. "
            "If unknown, say you cannot find it in the statement."
        )
        user = f"Context:\n{context}\n\nQuestion: {question}"
    else:
        system = "You are a practical personal finance advisor."
        user = question

    answer_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
    )
    answer = answer_resp.choices[0].message.content.strip()
    return answer, route


app = FastAPI(title="PocketWatch Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    content = await file.read()
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
def chat(payload: ChatRequest) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    session = SESSIONS.get(payload.sessionId) if payload.sessionId else None
    answer, route = _chat_with_model(question, session)
    return {"answer": answer, "route": route}


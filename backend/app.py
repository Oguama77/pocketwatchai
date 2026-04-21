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

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None


_TESSERACT_PROBE_CACHE: dict[str, bool] = {}


def _locate_tesseract_binary() -> str | None:
    """Best-effort auto-discovery of the Tesseract executable.

    Checks (in order):
      1. An explicit TESSERACT_CMD env var,
      2. the value already configured on pytesseract (PATH lookup),
      3. standard Windows install paths,
      4. standard macOS / Linux paths.
    """
    if pytesseract is None:
        return None

    import shutil

    env = os.getenv("TESSERACT_CMD")
    if env and os.path.isfile(env):
        return env

    configured = pytesseract.pytesseract.tesseract_cmd
    if configured:
        found = shutil.which(configured)
        if found:
            return found
        if os.path.isfile(configured):
            return configured

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _ocr_available() -> bool:
    """Check whether OCR can actually run: pytesseract importable AND the Tesseract binary present."""
    if pytesseract is None or pdfium is None:
        return False
    cached = _TESSERACT_PROBE_CACHE.get("ok")
    if cached is not None:
        return cached
    binary = _locate_tesseract_binary()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary
    try:
        pytesseract.get_tesseract_version()
        _TESSERACT_PROBE_CACHE["ok"] = True
        return True
    except Exception:
        _TESSERACT_PROBE_CACHE["ok"] = False
        return False


def _ocr_install_hint() -> str:
    """Actionable install hint when OCR deps (or the Tesseract binary) are missing."""
    missing = []
    if pytesseract is None:
        missing.append("the 'pytesseract' Python package")
    if pdfium is None:
        missing.append("the 'pypdfium2' Python package")
    if missing:
        return (
            f"Install {', '.join(missing)} (see backend/requirements.txt) and restart the server."
        )
    return (
        "The Tesseract OCR binary was not found. Install it and make sure it is on PATH, "
        "or set TESSERACT_CMD to its full path:\n"
        "  - Windows: https://github.com/UB-Mannheim/tesseract/wiki "
        r"(default install path C:\Program Files\Tesseract-OCR\tesseract.exe)"
        "\n  - macOS:   brew install tesseract"
        "\n  - Ubuntu:  sudo apt-get install tesseract-ocr"
    )


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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str
    sessionId: str | None = None
    history: list[ChatMessage] | None = None


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


def _parse_money(val: object) -> float | None:
    """Parse a single money cell, robust to:
      * Currency symbols / words (€, $, £, EUR, USD, ...)
      * European format (1.234,56) and US format (1,234.56)
      * Parentheses for negatives, e.g. "(123.45)"
      * Trailing "DR" (debit/negative) and "CR" (credit/positive) markers
      * Spaces, NBSPs, blank cells.
    Returns None for unparseable / empty cells.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(f) else f

    s = str(val).strip().replace("\u00a0", " ")
    if not s or s in {"-", "—", "–", "nan", "NaN", "None"}:
        return None

    is_neg = False
    if "(" in s and ")" in s:
        is_neg = True
        s = s.replace("(", "").replace(")", "")

    upper = s.upper()
    if re.search(r"\bDR\b", upper):
        is_neg = True

    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s or s in {"-", ".", ","}:
        return None

    if s.startswith("-"):
        is_neg = True
        s = s[1:]
    s = s.replace("-", "")

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        n = float(s)
    except ValueError:
        return None
    return -n if is_neg else n


def _to_float(series: pd.Series) -> pd.Series:
    parsed = series.map(_parse_money)
    return pd.to_numeric(parsed, errors="coerce")


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
    """Process every page of the uploaded PDF by default.

    MAX_PDF_PAGES can still override the cap via env if a deployment wants a
    hard safety limit, but the default is effectively unlimited so long
    statements (e.g. 50+ pages) are no longer truncated."""
    try:
        value = int(os.getenv("MAX_PDF_PAGES", "100000"))
        return max(1, value)
    except ValueError:
        return 100000


def _pdf_time_budget_seconds() -> float:
    """No enforced processing budget by default.

    OCR of scanned PDFs is slow (several seconds per page). PDF_TIME_BUDGET_SECONDS
    can still set an explicit cap for deployments that need one, but we no longer
    cut parsing short on purpose."""
    raw = os.getenv("PDF_TIME_BUDGET_SECONDS")
    if raw is None:
        return float("inf")
    try:
        value = float(raw)
        return value if value > 0 else float("inf")
    except ValueError:
        return float("inf")


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


_HEADER_KEYWORDS: set[str] = {
    "date", "transaction", "trans", "transdate", "valuedate", "bookingdate",
    "postdate", "postingdate", "txn",
    "description", "details", "narrative", "memo", "reference", "particulars",
    "debit", "credit", "amount", "money", "balance", "withdrawal", "deposit",
    "in", "out", "payee",
}

_MONTH_PATTERN = (
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|October|November|December"
)

_DATE_LINE_RE = re.compile(
    r"^\s*(?:"
    rf"(?:{_MONTH_PATTERN})\s+\d{{1,2}},?\s+\d{{2,4}}|"
    rf"\d{{1,2}}[\s\-/\.](?:{_MONTH_PATTERN})[\s\-/\.]\d{{2,4}}|"
    r"\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4}|"
    r"\d{4}[\-/\.]\d{1,2}[\-/\.]\d{1,2}"
    r")\b",
    re.I,
)


def _is_date_header_token(token: str) -> bool:
    return token == "date" or token.endswith("date") or token.startswith("date")


def _group_words_by_line(words: list[dict], y_tol: float = 2.5) -> list[list[dict]]:
    """Cluster pdfplumber words into visual lines based on their 'top' coordinate."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = [[ws[0]]]
    for w in ws[1:]:
        if abs(w["top"] - lines[-1][-1]["top"]) <= y_tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda x: x["x0"])
    return lines


def _score_header_candidate_line(line_words: list[dict]) -> int:
    """Score how likely a line of words is the transaction table's header row."""
    if len(line_words) < 3:
        return 0
    toks = [re.sub(r"[^a-z]", "", str(w["text"]).lower()) for w in line_words]
    if not any(_is_date_header_token(t) for t in toks if t):
        return 0
    hits = sum(1 for t in toks if t in _HEADER_KEYWORDS)
    if hits < 3:
        return 0
    amount_like = sum(
        1 for t in toks
        if t in {"amount", "debit", "credit", "balance", "withdrawal", "deposit", "money"}
    )
    return hits + amount_like


def _pick_column_gap_threshold(gaps: list[float]) -> float:
    """Find the jump between intra-column and inter-column word gaps.

    Sort gaps ascending; the biggest *ratio* between consecutive values marks
    the boundary. If the distribution is uniform (no clear jump), fall back to
    a fixed 15pt threshold, which empirically separates columns on most bank
    statements without merging multi-word header cells like "Money out"."""
    if not gaps:
        return float("inf")
    sorted_g = sorted(gaps)
    if len(sorted_g) == 1:
        return sorted_g[0] + 1.0
    best_ratio = 1.0
    threshold = sorted_g[-1] + 1.0
    for i in range(1, len(sorted_g)):
        prev = max(sorted_g[i - 1], 0.5)
        ratio = sorted_g[i] / prev
        if ratio > best_ratio:
            best_ratio = ratio
            threshold = (sorted_g[i - 1] + sorted_g[i]) / 2
    if best_ratio < 1.8:
        return 15.0
    return max(threshold, 6.0)


def _infer_columns_from_header_words(header_words: list[dict]) -> list[tuple[str, float, float]]:
    """Group adjacent header words into column cells using a dynamic x-gap threshold."""
    if not header_words:
        return []
    gaps = [header_words[i]["x0"] - header_words[i - 1]["x1"] for i in range(1, len(header_words))]
    threshold = _pick_column_gap_threshold(gaps)
    groups: list[list[dict]] = [[header_words[0]]]
    for i, gap in enumerate(gaps, start=1):
        if gap >= threshold:
            groups.append([header_words[i]])
        else:
            groups[-1].append(header_words[i])
    return [(" ".join(w["text"] for w in g), g[0]["x0"], g[-1]["x1"]) for g in groups]


def _assign_word_to_column(word: dict, columns: list[tuple[str, float, float]]) -> int:
    """Pick the column whose x-range overlaps the word most; fall back to the rightmost
    column whose x-start lies at or before the word (natural reading order)."""
    x0, x1 = word["x0"], word["x1"]
    best_col = -1
    best_overlap = 0.0
    for i, (_, xs, xe) in enumerate(columns):
        overlap = max(0.0, min(x1, xe) - max(x0, xs))
        if overlap > best_overlap:
            best_overlap = overlap
            best_col = i
    if best_col >= 0:
        return best_col
    center = (x0 + x1) / 2
    picked = 0
    for i, (_, xs, _) in enumerate(columns):
        if xs <= center + 4:
            picked = i
    return picked


def _extract_transactions_from_words(words: list[dict], y_tol: float = 2.5) -> list[list[str]] | None:
    """Reconstruct a transaction table from positioned words (pdfplumber or OCR).

    Works for PDFs where pdfplumber's ruled-table extraction fails or mangles
    rows (Revolut has no table rules at all; FirstBank has rules but rows are
    collapsed into a single cell), AND for OCR output from scanned PDFs where
    Tesseract provides per-word bounding boxes. Steps:
      1. Group words into visual lines.
      2. Detect the header line (must mention a 'date' column + >=3 known terms).
      3. Derive column x-ranges from header word clusters.
      4. For every subsequent line, assign words to columns by x-overlap; a new
         transaction begins when the date column matches a date pattern,
         otherwise the line is a continuation of the current transaction.
    """
    if not words:
        return None

    lines = _group_words_by_line(words, y_tol=y_tol)
    header_idx = -1
    best_score = 0
    for i, line in enumerate(lines):
        s = _score_header_candidate_line(line)
        if s > best_score:
            best_score = s
            header_idx = i
    if header_idx < 0:
        return None

    columns = _infer_columns_from_header_words(lines[header_idx])
    if len(columns) < 2:
        return None

    date_col_idx = 0
    for i, (lbl, _, _) in enumerate(columns):
        norm = re.sub(r"[^a-z]", "", lbl.lower())
        if _is_date_header_token(norm):
            date_col_idx = i
            break
    else:
        for i, (lbl, _, _) in enumerate(columns):
            if "date" in lbl.lower():
                date_col_idx = i
                break

    rows: list[list[str]] = []
    current: list[list[str]] | None = None
    for line in lines[header_idx + 1:]:
        cell_tokens: list[list[str]] = [[] for _ in columns]
        for w in line:
            col = _assign_word_to_column(w, columns)
            cell_tokens[col].append(w["text"])
        cells = [" ".join(tokens).strip() for tokens in cell_tokens]

        date_cell = cells[date_col_idx] if date_col_idx < len(cells) else ""
        if date_cell and _DATE_LINE_RE.match(date_cell):
            if current is not None:
                rows.append([" ".join(c).strip() for c in current])
            current = [[cells[j]] if cells[j] else [] for j in range(len(columns))]
        elif current is not None:
            for j in range(len(columns)):
                if cells[j]:
                    current[j].append(cells[j])

    if current is not None:
        rows.append([" ".join(c).strip() for c in current])
    if not rows:
        return None
    header_labels = [c[0] for c in columns]
    return [header_labels] + rows


def _extract_transactions_by_word_positions(page) -> list[list[str]] | None:
    """Thin adapter: pull pdfplumber words from a native Page and parse them."""
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception:
        return None
    return _extract_transactions_from_words(words, y_tol=2.5)


def _preprocess_for_ocr(img):
    """Boost OCR accuracy on scanned statements with minimal peak memory.

    The input is already 'L' (grayscale) mode — we render that way directly
    from pypdfium2. We apply autocontrast + median denoise + threshold in
    place, reusing buffers so at most one full-resolution image exists in RAM
    at a time. This matters on small hosts (e.g. 512 MB Render Starter) where
    a 300 DPI A4 page is ~8.5M pixels — holding 3 copies briefly is enough to
    OOM on a 28-page scan like a UBA statement."""
    try:
        from PIL import ImageOps, ImageFilter
    except Exception:
        return img
    try:
        gray = img if img.mode == "L" else img.convert("L")
        if gray is not img:
            img.close()
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
        bw = gray.point(lambda p: 255 if p > 180 else 0, mode="1")
        gray.close()
        return bw
    except Exception:
        return img


def _get_ocr_dpi() -> int:
    try:
        return max(120, min(600, int(os.getenv("OCR_DPI", "200"))))
    except ValueError:
        return 200


def _ocr_page_words_from_doc(pdf_doc, page_idx: int, dpi: int | None = None) -> list[dict] | None:
    """OCR a single page of an already-open pypdfium2 PdfDocument.

    Factored out so the caller can open the document once for the whole file
    instead of reparsing it per page (the old code did N full parses). Returns
    words in pdfplumber-style dicts in typographic points (72 dpi), so the
    word-position parser works unchanged.

    Careful with memory: we render at `force_bitmap_format=GRAY` to avoid an
    intermediate RGBA bitmap (4x smaller), and we explicitly close bitmaps
    and images as soon as possible."""
    if pytesseract is None or pdfium is None:
        return None
    if dpi is None:
        dpi = _get_ocr_dpi()
    try:
        if page_idx >= len(pdf_doc):
            return None
        page = pdf_doc[page_idx]
    except Exception:
        return None

    scale = dpi / 72.0
    img = None
    bitmap = None
    try:
        try:
            try:
                bitmap = page.render(
                    scale=scale,
                    grayscale=True,
                )
            except TypeError:
                # Older pypdfium2 API: no `grayscale` kwarg.
                bitmap = page.render(scale=scale)
            img = bitmap.to_pil()
        except Exception:
            return None
        finally:
            if bitmap is not None:
                try:
                    bitmap.close()
                except Exception:
                    pass
                bitmap = None

        img = _preprocess_for_ocr(img)
        tesseract_config = os.getenv(
            "TESSERACT_CONFIG",
            "--oem 1 --psm 6 -c preserve_interword_spaces=1",
        )
        try:
            data = pytesseract.image_to_data(
                img,
                output_type=pytesseract.Output.DICT,
                config=tesseract_config,
                lang=os.getenv("TESSERACT_LANG", "eng"),
            )
        except pytesseract.TesseractNotFoundError:
            raise
        except Exception:
            return None
    finally:
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
        try:
            page.close()
        except Exception:
            pass

    n = len(data.get("text", []))
    if n == 0:
        return None
    inv_scale = 72.0 / dpi
    words: list[dict] = []
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = int(data.get("conf", ["-1"])[i])
        except (ValueError, TypeError):
            conf = -1
        if conf < 30:
            continue
        left = float(data["left"][i]) * inv_scale
        top = float(data["top"][i]) * inv_scale
        width = float(data["width"][i]) * inv_scale
        height = float(data["height"][i]) * inv_scale
        words.append({
            "text": txt,
            "x0": left,
            "x1": left + width,
            "top": top,
            "bottom": top + height,
        })
    return words or None


def _ocr_page_words(pdf_bytes: bytes, page_idx: int, dpi: int | None = None) -> list[dict] | None:
    """Convenience wrapper: open the PDF for a single page. Prefer using
    `_ocr_page_words_from_doc` directly when OCR'ing many pages in a row."""
    if pytesseract is None or pdfium is None:
        return None
    try:
        pdf_doc = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return None
    try:
        return _ocr_page_words_from_doc(pdf_doc, page_idx, dpi=dpi)
    finally:
        try:
            pdf_doc.close()
        except Exception:
            pass


def _ocr_page_text(words: list[dict] | None) -> str:
    """Reconstruct plain-text per page from OCR words (line-grouped, x-sorted)."""
    if not words:
        return ""
    lines = _group_words_by_line(words, y_tol=3.0)
    return "\n".join(" ".join(w["text"] for w in line) for line in lines)


def _table_quality_score(tbl: list[list[str]]) -> float:
    """Rank a candidate table by how much it looks like a usable transaction list.

    Hard gates:
      * header must contain a 'date' column, and
      * header must contain at least 2 transaction-related terms, and
      * at least 3 distinct columns must be populated in the body.
    Tables failing any gate score 0, which eliminates pdfplumber's
    text-strategy output from random PDF prose (e.g. address block at the top
    of a Revolut statement) that otherwise wins on raw row count."""
    if not tbl or len(tbl) < 2:
        return 0.0
    header = tbl[0]
    body = tbl[1:]
    width = max(len(r) for r in tbl)
    if width < 2:
        return 0.0

    col_fill = [0] * width
    for r in body:
        for i, c in enumerate(r[:width]):
            if c is not None and str(c).strip():
                col_fill[i] += 1
    populated_cols = sum(1 for c in col_fill if c > 0)
    if populated_cols < 3:
        return 0.0

    hdr_norm = [_norm_col(str(c)) for c in header]
    transaction_terms = (
        "date", "description", "details", "narrative",
        "amount", "debit", "credit", "balance",
        "withdrawal", "deposit", "money",
    )
    hdr_hits = sum(
        1 for c in hdr_norm
        if c and any(tok in c for tok in transaction_terms)
    )
    if hdr_hits < 2:
        return 0.0
    has_date_header = any(c and "date" in c for c in hdr_norm)
    if not has_date_header:
        return 0.0

    body_rows = min(len(body), 300)
    return populated_cols * 5 + body_rows + hdr_hits * 40


def _extract_tables_from_pdf_page(page) -> list[list[list[str]]]:
    """Return the single best transaction-like table for a page (list-wrapped).

    Runs a word-position reconstructor plus several pdfplumber strategies and
    picks the highest-scoring candidate. This generalises across statements
    that extract cleanly with ruled tables (e.g. FirstBank) and ones with no
    table lines at all (e.g. Revolut)."""
    candidates: list[list[list[str]]] = []
    seen: set[tuple[str, ...]] = set()

    def add_candidate(raw: list[list] | None) -> None:
        if not raw or len(raw) < 2:
            return
        norm = [_row_cells(r) for r in raw]
        fp = _table_fingerprint(norm)
        if fp in seen:
            return
        seen.add(fp)
        candidates.append(norm)

    try:
        wp = _extract_transactions_by_word_positions(page)
    except Exception:
        wp = None
    if wp:
        add_candidate(wp)

    strategies: tuple[dict | None, ...] = (
        None,
        {"vertical_strategy": "text", "horizontal_strategy": "lines"},
        {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        {"vertical_strategy": "lines", "horizontal_strategy": "text", "intersection_tolerance": 8},
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
    )
    for ts in strategies:
        try:
            raws = page.extract_tables(table_settings=ts) if ts else page.extract_tables()
        except Exception:
            continue
        for raw in (raws or []):
            add_candidate(raw)

    if not candidates:
        return []
    best = max(candidates, key=_table_quality_score)
    return [best] if _table_quality_score(best) > 0 else []


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
    """Extract tables from every page of the uploaded PDF.

    Strategy:
      * For pages that have extractable text, use pdfplumber + the scored
        table/word-position pipeline.
      * For pages that have no extractable text (scanned / image-only), fall
        back to OCR via pytesseract + pypdfium2 and feed the OCR word boxes
        through the same word-position transaction parser.

    No artificial page cap or wall-clock budget is applied by default, so long
    or scanned statements are processed fully. MAX_PDF_PAGES and
    PDF_TIME_BUDGET_SECONDS env vars still work as optional safety limits."""
    import time as _time
    import gc as _gc

    max_pages = _max_pdf_pages()
    time_budget = _pdf_time_budget_seconds()
    started = _time.monotonic()

    all_rows: list[list[str]] = []
    text_chunks: list[str] = []
    pages_processed = 0
    pages_with_text = 0
    pages_ocred = 0
    timed_out = False
    ocr_attempted = False
    ocr_ready = False
    tesseract_missing = False
    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not open PDF (file may be corrupted or password-protected): {e}",
        )

    # Open the pypdfium2 document lazily — only if / when we hit an OCR page.
    # Opening it once here and reusing it across every page avoids re-parsing
    # the PDF N times (the old code did that inside _ocr_page_words).
    pdfium_doc = None

    try:
        with pdf_ctx as pdf:
            total = len(pdf.pages)
            for page_idx, page in enumerate(pdf.pages[:max_pages]):
                if _time.monotonic() - started > time_budget:
                    timed_out = True
                    break
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                has_chars = bool(getattr(page, "chars", None))
                pages_processed += 1

                chunk: list[list[str]] = []
                if page_text.strip() or has_chars:
                    pages_with_text += 1
                    text_chunks.append(page_text)
                    try:
                        page_tables = _extract_tables_from_pdf_page(page)
                    except Exception:
                        page_tables = []
                    if page_tables:
                        best_tbl = max(page_tables, key=len)
                        chunk = _merge_flat_rows_from_tables([best_tbl])
                else:
                    # Image-only page → OCR.
                    if not ocr_attempted:
                        ocr_attempted = True
                        ocr_ready = _ocr_available()
                        print(
                            f"[pdf] page {page_idx + 1}/{total} has no text; "
                            f"OCR available={ocr_ready} "
                            f"dpi={_get_ocr_dpi()} "
                            f"(tesseract={pytesseract.pytesseract.tesseract_cmd if pytesseract else 'missing'})"
                        )
                        if ocr_ready and pdfium_doc is None:
                            try:
                                pdfium_doc = pdfium.PdfDocument(content)
                            except Exception as open_err:
                                print(f"[pdf] pdfium open failed: {open_err!r}")
                                ocr_ready = False
                    if not ocr_ready or pdfium_doc is None:
                        text_chunks.append("")
                        continue
                    page_started = _time.monotonic()
                    try:
                        words = _ocr_page_words_from_doc(pdfium_doc, page_idx)
                    except Exception as ocr_err:
                        if pytesseract is not None and isinstance(
                            ocr_err, pytesseract.TesseractNotFoundError
                        ):
                            tesseract_missing = True
                            ocr_ready = False
                        words = None
                    page_elapsed = _time.monotonic() - page_started
                    if words:
                        pages_ocred += 1
                        text_chunks.append(_ocr_page_text(words))
                        reconstructed = _extract_transactions_from_words(words, y_tol=4.0)
                        if reconstructed and len(reconstructed) >= 2:
                            chunk = _merge_flat_rows_from_tables([reconstructed])
                        print(
                            f"[pdf] ocr page {page_idx + 1}: {len(words)} words, "
                            f"table_rows={len(reconstructed) if reconstructed else 0}, "
                            f"{page_elapsed:.1f}s"
                        )
                    else:
                        text_chunks.append("")
                        print(f"[pdf] ocr page {page_idx + 1}: no words recognised ({page_elapsed:.1f}s)")
                    # Force cleanup of the page-sized OCR buffers before moving on
                    # so peak RSS stays close to one page worth on small hosts.
                    _gc.collect()

                if len(chunk) > 1:
                    if not all_rows:
                        all_rows.extend(chunk)
                    else:
                        h0 = all_rows[0]
                        start_row = 1 if _rows_equal_as_header(chunk[0], h0) else 0
                        all_rows.extend(chunk[start_row:])
    finally:
        if pdfium_doc is not None:
            try:
                pdfium_doc.close()
            except Exception:
                pass

    if len(all_rows) < 2:
        if tesseract_missing:
            hint = (
                "This PDF is scanned/image-only and the Tesseract OCR binary could not be found. "
                + _ocr_install_hint()
            )
        elif pages_processed > 0 and pages_with_text == 0 and not ocr_ready:
            hint = (
                f"This PDF looks scanned/image-only ({pages_processed} page(s), no extractable text). "
                "OCR is not available on this server. " + _ocr_install_hint()
            )
        elif pages_processed > 0 and pages_with_text == 0 and pages_ocred == 0:
            hint = (
                f"OCR ran on {pages_processed} scanned page(s) but could not recognise any usable transaction table. "
                "Try a higher-quality scan or export the statement as CSV/XLSX from your bank."
            )
        elif timed_out:
            hint = (
                f"PDF processing exceeded the {int(time_budget)}s time budget after {pages_processed} page(s). "
                "Unset PDF_TIME_BUDGET_SECONDS to run without a budget, or split the PDF."
            )
        else:
            hint = (
                "No usable transaction table found in this PDF. "
                "If this is a text PDF, the header may not contain a recognisable 'date' column — "
                "try a CSV/XLSX export. If it's a scan, make sure Tesseract OCR is installed. "
                + _ocr_install_hint()
            )
        raise HTTPException(status_code=400, detail=hint)
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


CURRENCY_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "₺": "TRY",
    "₪": "ILS",
    "₦": "NGN",
    "₴": "UAH",
    "฿": "THB",
}

CURRENCY_DOLLAR_PREFIX: dict[str, str] = {
    "CA$": "CAD",
    "C$": "CAD",
    "AU$": "AUD",
    "A$": "AUD",
    "NZ$": "NZD",
    "HK$": "HKD",
    "SG$": "SGD",
    "S$": "SGD",
    "R$": "BRL",
    "MX$": "MXN",
    "NT$": "TWD",
    "US$": "USD",
}

CURRENCY_CODE_RE = re.compile(
    r"\b(EUR|USD|GBP|JPY|INR|CHF|CAD|AUD|NZD|SEK|NOK|DKK|ZAR|HKD|SGD|CNY|RMB|"
    r"KRW|BRL|MXN|RUB|PLN|TRY|ILS|AED|SAR|QAR|CZK|HUF|RON|IDR|THB|MYR|PHP|VND|"
    r"PKR|BDT|NGN|KES|GHS|EGP|MAD|TWD|UAH|ARS|COP|CLP|PEN|ISK|BGN|HRK)\b"
)


def _detect_currency(source_text: str, df: pd.DataFrame | None) -> str:
    """Best-effort currency detection from PDF text, column headers, and cell values.

    Returns an empty string when no currency evidence is found, so the frontend
    can render totals without a misleading default (e.g. EUR)."""
    samples: list[str] = []
    if source_text:
        samples.append(source_text[:20000])
    if df is not None:
        samples.extend(str(c) for c in df.columns)
        for col in df.columns:
            try:
                samples.extend(df[col].dropna().astype(str).head(50).tolist())
            except Exception:
                continue
    blob = " ".join(samples)
    if not blob.strip():
        return ""

    counts: dict[str, int] = {}

    for prefix, code in CURRENCY_DOLLAR_PREFIX.items():
        n = blob.count(prefix)
        if n:
            counts[code] = counts.get(code, 0) + n * 3

    for m in CURRENCY_CODE_RE.finditer(blob):
        code = m.group(1)
        if code == "RMB":
            code = "CNY"
        counts[code] = counts.get(code, 0) + 2

    for sym, code in CURRENCY_SYMBOLS.items():
        n = blob.count(sym)
        if n:
            counts[code] = counts.get(code, 0) + n

    dollar_count = blob.count("$")
    prefixed_dollars = sum(blob.count(p) for p in CURRENCY_DOLLAR_PREFIX)
    bare_dollars = dollar_count - prefixed_dollars
    if bare_dollars > 0 and "USD" not in counts and not any(
        counts.get(c, 0) >= 3 for c in ("CAD", "AUD", "NZD", "HKD", "SGD")
    ):
        counts["USD"] = counts.get("USD", 0) + bare_dollars

    if not counts:
        return ""
    return max(counts, key=lambda k: counts[k])


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

    has_debit = "debit" in df.columns
    has_credit = "credit" in df.columns
    has_amount = "amount" in df.columns
    has_balance = "balance" in df.columns

    expense = pd.Series(0.0, index=df.index)
    income = pd.Series(0.0, index=df.index)

    if has_debit:
        expense = df["debit"].fillna(0).abs()
    if has_credit:
        income = df["credit"].fillna(0).abs()

    if not has_debit and has_amount:
        expense = df["amount"].where(df["amount"] < 0, other=0).abs().fillna(0)
    if not has_credit and has_amount:
        income = df["amount"].where(df["amount"] > 0, other=0).fillna(0)

    if not has_debit and not has_credit and not has_amount and has_balance:
        diff = (df["balance"].shift(1) - df["balance"]).fillna(0)
        expense = diff.clip(lower=0)
        income = (-diff).clip(lower=0)

    df["expense"] = pd.to_numeric(expense, errors="coerce").fillna(0)
    df["income"] = pd.to_numeric(income, errors="coerce").fillna(0)
    df["description"] = df["description"].astype(str).fillna("")
    return df, ""


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

    daily_spending = work.groupby(work["date"].dt.date)["expense"].sum()
    top_days = daily_spending[daily_spending > 0].sort_values(ascending=False).head(5)
    top_spending_days = [
        {"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "amount": float(amt)}
        for d, amt in top_days.items()
    ]

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
        "topSpendingDays": top_spending_days,
        "categoryBreakdown": [
            {"name": name, "value": float(value)} for name, value in major.items()
        ],
    }


def _format_history(history: list[ChatMessage] | None, max_turns: int = 8) -> str:
    """Render recent conversation as a readable transcript for prompt injection."""
    if not history:
        return ""
    recent = history[-max_turns * 2 :]
    lines: list[str] = []
    for msg in recent:
        role = (msg.role or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


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


def _chat_with_model(
    question: str,
    session: SessionData | None,
    history: list[ChatMessage] | None = None,
) -> tuple[str, str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return _fallback_chat(question, session)

    history_text = _format_history(history)
    history_block = f"Recent conversation (for context):\n{history_text}\n\n" if history_text else ""

    # If LangChain or tooling deps are unavailable, keep the legacy OpenAI fallback path.
    if ChatOpenAI is None or tool is None:
        client = OpenAI(api_key=api_key)
        chat_messages = [
            {"role": "system", "content": "You are a practical personal finance advisor. Use the prior conversation for context when the user asks follow-up questions."},
        ]
        if history:
            for m in history[-16:]:
                role = (m.role or "").lower()
                if role in {"user", "assistant"} and (m.content or "").strip():
                    chat_messages.append({"role": role, "content": m.content})
        chat_messages.append({"role": "user", "content": question})
        answer_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=chat_messages,
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
            "with practical examples when useful. Use the prior conversation to resolve "
            "follow-up references (pronouns like 'it', 'that', 'them' or implicit topics). "
            "If a question needs up-to-date facts, say so briefly.\n\n"
            f"{history_block}Current question: {question}"
        )
        return str(resp.content).strip()

    @tool("general_finance_realtime_web")
    def general_finance_realtime_web(question: str) -> str:
        """Use for finance questions requiring current or recent information from the web."""
        search_query = question
        if history_text:
            search_query = f"{question} (context: {history_text[-400:]})"
        search_results: str = ""
        if search_tool is not None:
            try:
                search_results = str(search_tool.invoke(search_query))
            except Exception as e:  # pragma: no cover
                search_results = f"(search failed: {e})"
        if not search_results:
            resp = llm.invoke(
                "You are a careful finance assistant. The server has no live web search available right now. "
                "Use the prior conversation for context. Answer with caveats about potentially "
                "outdated information and suggest the user verify current data from an authoritative source.\n\n"
                f"{history_block}Current question: {question}"
            )
            return str(resp.content).strip()
        resp = llm.invoke(
            "You are a finance assistant using web results. Answer with current facts, "
            "use prior conversation to resolve follow-ups, mention uncertainty where relevant, "
            "and include source URLs inline when possible.\n\n"
            f"{history_block}Current question: {question}\n\nWeb results:\n{search_results}"
        )
        return str(resp.content).strip()

    def _structured_statement_answer(df: pd.DataFrame, currency: str) -> str:
        """Ask the planner for a precise numeric answer; return '' if it can't."""
        try:
            planner = llm.with_structured_output(CalcPlan)
            plan = planner.invoke(
                "Extract a calculation plan from the user question. "
                "Use the prior conversation to resolve follow-up references such as 'that day', "
                "'the same week', or implicit dates carried from previous turns.\n"
                "Operations: sum_metric_on_date, total_metric, sum_metric_between_dates, avg_daily_metric.\n"
                "Metric must be one of expense,income,balance,amount.\n"
                "Return ISO dates (YYYY-MM-DD) when dates are provided.\n\n"
                f"{history_block}Current question: {question}"
            )
        except Exception:
            return ""
        metric = plan.metric if plan.metric in {"expense", "income", "balance", "amount"} else "expense"
        if metric not in df.columns:
            return ""
        if plan.operation == "sum_metric_on_date":
            d = _parse_date_safe(plan.date)
            if d is None:
                return ""
            mask = df["date"].dt.date == d.date()
            total = float(df.loc[mask, metric].sum())
            return f"{metric} on {d.date().isoformat()} = {currency} {total:,.2f}"
        if plan.operation == "sum_metric_between_dates":
            d0, d1 = _parse_date_safe(plan.start_date), _parse_date_safe(plan.end_date)
            if d0 is None or d1 is None:
                return ""
            mask = (df["date"] >= d0) & (df["date"] <= d1)
            total = float(df.loc[mask, metric].sum())
            return (
                f"{metric} from {d0.date().isoformat()} to {d1.date().isoformat()} "
                f"= {currency} {total:,.2f}"
            )
        if plan.operation == "avg_daily_metric" and len(df):
            by_day = df.groupby(df["date"].dt.date)[metric].sum()
            avg = float(by_day.mean()) if len(by_day) else 0.0
            return f"average daily {metric} = {currency} {avg:,.2f}"
        total = float(df[metric].sum())
        return f"total {metric} = {currency} {total:,.2f}"

    @tool("document_statement_qa")
    def document_statement_qa(question: str) -> str:
        """Answer ANY question about the user's uploaded bank statement.

        Covers sums, date-range queries, top transactions, category breakdowns,
        narrative questions ("what's my biggest recurring expense?"), and lookups
        of specific entries. Grounds answers in both the structured transactions
        and the raw extracted statement text."""
        if not session:
            return (
                "No bank statement is currently loaded on the server. "
                "Upload one on the dashboard, then ask this question again."
            )

        df = session.frame.copy()
        currency = session.currency or ""

        computed = _structured_statement_answer(df, currency)

        retrieval_query = question if not history_text else f"{question}\n{history_text[-400:]}"
        selected = _retrieve_chunks(retrieval_query, session.source_text, top_k=4)
        text_context = "\n\n---\n\n".join(selected) if selected else "(no raw statement text available)"

        summary = session.analytics.get("summary", {})
        summary_text = (
            f"Total income: {currency} {summary.get('totalIncome', 0):,.2f}\n"
            f"Total expenses: {currency} {summary.get('totalExpenses', 0):,.2f}\n"
            f"Net balance: {currency} {summary.get('netBalance', 0):,.2f}"
        )
        sample_cols = [c for c in ("date", "description", "expense", "income", "amount", "balance") if c in df.columns]
        sample_csv = df[sample_cols].head(80).to_csv(index=False) if sample_cols else df.head(80).to_csv(index=False)

        prompt = (
            "You are a financial assistant answering questions strictly about the user's "
            "uploaded bank statement. Prefer the structured transaction sample for "
            "calculations and the raw statement text for descriptive/narrative context. "
            "If a requested figure cannot be derived from the provided data, say so plainly "
            "and suggest a more precise question (e.g. a specific date range). "
            "Use the prior conversation to resolve follow-up references.\n\n"
            f"{history_block}"
            f"Current question: {question}\n\n"
            f"Statement summary:\n{summary_text}\n\n"
            f"Precomputed figure (may be empty if not applicable): {computed or '(none)'}\n\n"
            f"Transaction sample (first 80 rows, CSV):\n{sample_csv}\n\n"
            f"Relevant raw statement text:\n{text_context}"
        )
        resp = llm.invoke(prompt)
        return str(resp.content).strip()

    tools = [
        general_finance_timeless,
        general_finance_realtime_web,
        document_statement_qa,
    ]
    router = llm.bind_tools(tools, tool_choice="auto")
    session_hint = (
        "A bank statement IS currently loaded — strongly prefer document_statement_qa "
        "whenever the user is asking anything that could plausibly refer to their own "
        "transactions, balances, spending, income, or any numbers/entries on the statement."
        if session is not None
        else "NO bank statement is loaded — do NOT call document_statement_qa."
    )
    routing_input = (
        f"{session_hint}\n\n"
        f"{history_block}Current question: {question}\n\n"
        "Pick the single best tool to answer the current question, taking the recent "
        "conversation into account so follow-ups are routed consistently with prior turns."
    )
    routed = router.invoke(routing_input)
    if not getattr(routed, "tool_calls", None):
        # No tool chosen — pick a sensible default based on whether a statement is loaded.
        if session is not None:
            return document_statement_qa.invoke({"question": question}), "document", "document_statement_qa"
        return general_finance_timeless.invoke({"question": question}), "general", "general_finance_timeless"

    call = routed.tool_calls[0]
    tool_name = str(call.get("name", ""))
    tool_args = call.get("args", {}) or {}
    tool_map: dict[str, tuple[Callable, str]] = {
        "general_finance_timeless": (general_finance_timeless, "general"),
        "general_finance_realtime_web": (general_finance_realtime_web, "general"),
        "document_statement_qa": (document_statement_qa, "document"),
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


@app.get("/api/ocr-status")
def ocr_status() -> dict:
    """Diagnostics: confirms whether OCR will work on *this* server instance.

    Hit this after deploy to verify the Tesseract binary was installed and is
    reachable — if `available` is false, scanned PDFs cannot be OCR'd here
    regardless of what's installed locally."""
    import shutil

    binary_path = _locate_tesseract_binary() if pytesseract is not None else None
    version = None
    available = False
    error: str | None = None
    if pytesseract is not None:
        if binary_path:
            pytesseract.pytesseract.tesseract_cmd = binary_path
        try:
            version = str(pytesseract.get_tesseract_version())
            available = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    return {
        "available": available,
        "pytesseract_installed": pytesseract is not None,
        "pypdfium2_installed": pdfium is not None,
        "tesseract_cmd": binary_path or (pytesseract.pytesseract.tesseract_cmd if pytesseract else None),
        "tesseract_on_path": shutil.which("tesseract") is not None,
        "tesseract_version": version,
        "error": error,
        "env": {
            "TESSERACT_CMD": os.getenv("TESSERACT_CMD"),
            "TESSERACT_LANG": os.getenv("TESSERACT_LANG", "eng"),
            "OCR_DPI": os.getenv("OCR_DPI", "300"),
            "MAX_PDF_PAGES": os.getenv("MAX_PDF_PAGES"),
            "PDF_TIME_BUDGET_SECONDS": os.getenv("PDF_TIME_BUDGET_SECONDS"),
        },
        "install_hint": None if available else _ocr_install_hint(),
    }


@app.get("/")
def root() -> dict[str, str]:
    """Avoid 404 on GET / (browsers, probes, Render health checks sometimes hit /)."""
    return {"service": "pocketwatch-backend", "health": "/api/health"}


@app.get("/health")
def health_alias() -> dict[str, str]:
    return health()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file: {e}")

    max_bytes = _max_upload_bytes()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(content):,} bytes). Maximum is {max_bytes:,} bytes. "
                "Try a CSV/XLSX export from your bank, or split the PDF."
            ),
        )

    try:
        df_raw, source_text = _load_dataframe(file, content)
        detected_currency = _detect_currency(source_text, df_raw)
        df, _ = _prepare_financial_df(df_raw)
        currency = detected_currency
        analytics = _build_analytics(df, currency)
    except HTTPException:
        raise
    except MemoryError:
        raise HTTPException(
            status_code=413,
            detail=(
                "Server ran out of memory while parsing this file. "
                "Try a CSV/XLSX export, split the PDF into smaller files, or upgrade the host instance."
            ),
        )
    except Exception as e:
        import traceback
        print("[/api/upload] unexpected error:", repr(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=(
                f"Server error while processing the file: {type(e).__name__}: {e}. "
                "Try a CSV/XLSX export from your bank, or a smaller PDF."
            ),
        )

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
    import json as _json

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

    raw_history = payload_data.get("history")
    if isinstance(raw_history, str):
        try:
            payload_data["history"] = _json.loads(raw_history) if raw_history.strip() else None
        except Exception:
            payload_data["history"] = None

    try:
        payload = ChatRequest(**payload_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chat payload. Provide 'question' and optional 'sessionId'.")

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    session = SESSIONS.get(payload.sessionId) if payload.sessionId else None
    try:
        answer, route, selected_tool = _chat_with_model(question, session, payload.history)
    except Exception as e:  # pragma: no cover
        return {
            "answer": f"Chat failed on the server: {type(e).__name__}: {e}",
            "route": "general",
            "selectedTool": "error_fallback",
        }
    return {"answer": answer, "route": route, "selectedTool": selected_tool}


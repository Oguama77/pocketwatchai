import type { AnalyticsResponse } from "@/types/analytics";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export interface ChatResponse {
  answer: string;
  route: "document" | "general";
}

function assertHttpsApiInProduction(): void {
  if (typeof window === "undefined") return;
  if (window.location.protocol !== "https:") return;
  if (API_BASE.startsWith("http://") && !API_BASE.includes("127.0.0.1") && !API_BASE.includes("localhost")) {
    throw new Error(
      "VITE_API_BASE_URL must use https:// when the site is served over HTTPS (mixed content blocks the request).",
    );
  }
}

function mapNetworkError(url: string, err: unknown): Error {
  const hint =
    "Check that the backend is running, VITE_API_BASE_URL matches your API (HTTPS on production), " +
    "and Render CORS_ORIGINS includes this site's origin. Large PDFs can also crash a small instance—try CSV export or increase MAX_PDF_PAGES.";
  if (err instanceof DOMException && err.name === "AbortError") {
    return new Error("Upload cancelled.");
  }
  if (err instanceof TypeError) {
    return new Error(`Network error calling ${url}. ${hint} (${err.message})`);
  }
  if (err instanceof Error && err.message.toLowerCase().includes("fetch")) {
    return new Error(`Network error calling ${url}. ${hint}`);
  }
  return err instanceof Error ? err : new Error(String(err));
}

export async function uploadFinancialDocument(
  file: File,
  opts?: { signal?: AbortSignal },
): Promise<AnalyticsResponse> {
  assertHttpsApiInProduction();
  const form = new FormData();
  form.append("file", file);

  const url = `${API_BASE}/api/upload`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      body: form,
      signal: opts?.signal,
    });
  } catch (e) {
    throw mapNetworkError(url, e);
  }
  if (!res.ok) {
    throw new Error(await extractError(res, "Failed to upload document."));
  }
  return res.json();
}

export async function fetchAnalytics(sessionId: string): Promise<AnalyticsResponse> {
  assertHttpsApiInProduction();
  const url = `${API_BASE}/api/analytics/${sessionId}`;
  let res: Response;
  try {
    res = await fetch(url);
  } catch (e) {
    throw mapNetworkError(url, e);
  }
  if (!res.ok) {
    throw new Error(await extractError(res, "Failed to load analytics."));
  }
  return res.json();
}

export async function askFinanceQuestion(question: string, sessionId?: string): Promise<ChatResponse> {
  assertHttpsApiInProduction();
  const url = `${API_BASE}/api/chat`;
  const body = new URLSearchParams();
  body.set("question", question);
  if (sessionId) body.set("sessionId", sessionId);
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: body.toString(),
    });
  } catch (e) {
    throw mapNetworkError(url, e);
  }
  if (!res.ok) {
    throw new Error(await extractError(res, "Failed to get chat response."));
  }
  return res.json();
}

async function extractError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return data?.detail || fallback;
  } catch {
    return fallback;
  }
}

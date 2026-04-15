import type { AnalyticsResponse } from "@/types/analytics";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export interface ChatResponse {
  answer: string;
  route: "document" | "general";
}

export async function uploadFinancialDocument(file: File): Promise<AnalyticsResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw new Error(await extractError(res, "Failed to upload document."));
  }
  return res.json();
}

export async function fetchAnalytics(sessionId: string): Promise<AnalyticsResponse> {
  const res = await fetch(`${API_BASE}/api/analytics/${sessionId}`);
  if (!res.ok) {
    throw new Error(await extractError(res, "Failed to load analytics."));
  }
  return res.json();
}

export async function askFinanceQuestion(question: string, sessionId?: string): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, sessionId }),
  });
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

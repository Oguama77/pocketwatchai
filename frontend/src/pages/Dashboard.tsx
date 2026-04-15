import { AppLayout } from "@/components/layout/AppLayout";
import { SummaryCards } from "@/components/dashboard/SummaryCards";
import { SpendingChart } from "@/components/dashboard/SpendingChart";
import { CategoryChart } from "@/components/dashboard/CategoryChart";
import { FileUpload } from "@/components/dashboard/FileUpload";
import { useEffect, useState } from "react";
import type { AnalyticsResponse } from "@/types/analytics";
import { fetchAnalytics } from "@/lib/api";

const EMPTY_ANALYTICS: AnalyticsResponse = {
  sessionId: "",
  currency: "EUR",
  summary: {
    totalIncome: 0,
    totalExpenses: 0,
    netBalance: 0,
    incomeChangePct: 0,
    expenseChangePct: 0,
    netBalanceChangePct: 0,
  },
  spendingOverTime: [],
  categoryBreakdown: [],
};

export default function Dashboard() {
  const [analytics, setAnalytics] = useState<AnalyticsResponse>(EMPTY_ANALYTICS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const sessionId = localStorage.getItem("pocketwatch_session_id");
    if (!sessionId) return;
    setLoading(true);
    fetchAnalytics(sessionId)
      .then((res) => {
        setAnalytics(res);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load analytics.");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppLayout>
      <div className="p-6 max-w-7xl mx-auto space-y-6">
        <div>
          <h2 className="text-2xl font-bold mb-1">Welcome back, John</h2>
          <p className="text-muted-foreground text-sm">Here's your financial overview</p>
        </div>

        <FileUpload onUploaded={setAnalytics} />
        {error && <p className="text-sm text-destructive">{error}</p>}
        {loading && <p className="text-sm text-muted-foreground">Loading analytics...</p>}
        <SummaryCards summary={analytics.summary} currency={analytics.currency} />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <SpendingChart data={analytics.spendingOverTime} currency={analytics.currency} />
          <CategoryChart data={analytics.categoryBreakdown} currency={analytics.currency} />
        </div>
      </div>
    </AppLayout>
  );
}

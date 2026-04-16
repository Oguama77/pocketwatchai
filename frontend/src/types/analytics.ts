export interface SummaryMetrics {
  totalIncome: number;
  totalExpenses: number;
  netBalance: number;
  incomeChangePct: number;
  expenseChangePct: number;
  netBalanceChangePct: number;
}

export interface TopSpendingDay {
  date: string;
  amount: number;
}

export interface CategoryPoint {
  name: string;
  value: number;
}

export interface AnalyticsResponse {
  sessionId: string;
  currency: string;
  summary: SummaryMetrics;
  topSpendingDays: TopSpendingDay[];
  categoryBreakdown: CategoryPoint[];
}

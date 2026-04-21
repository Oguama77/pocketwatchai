import { TrendingUp, TrendingDown, Wallet } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { SummaryMetrics } from "@/types/analytics";

interface SummaryCardsProps {
  summary: SummaryMetrics;
  currency: string;
}

export function SummaryCards({ summary, currency }: SummaryCardsProps) {
  const prefix = currency ? `${currency} ` : "";
  const format = (n: number) =>
    `${prefix}${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

  const cards = [
    { title: "Total Income", value: format(summary.totalIncome), icon: TrendingUp },
    { title: "Total Expenses", value: format(summary.totalExpenses), icon: TrendingDown },
    { title: "Net Balance", value: format(summary.netBalance), icon: Wallet },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {cards.map((card, i) => (
        <Card
          key={card.title}
          className="shadow-card hover:shadow-card-hover transition-shadow duration-300 animate-fade-in"
          style={{ animationDelay: `${i * 100}ms` }}
        >
          <CardContent className="p-5">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-muted-foreground font-medium">{card.title}</span>
              <div className="h-9 w-9 rounded-lg bg-accent flex items-center justify-center">
                <card.icon className="h-4 w-4 text-accent-foreground" />
              </div>
            </div>
            <p className="text-2xl font-bold">{card.value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

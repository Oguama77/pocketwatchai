import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CategoryPoint } from "@/types/analytics";
import { useState } from "react";

interface CategoryChartProps {
  data: CategoryPoint[];
  currency: string;
}

const COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
  "hsl(var(--chart-6))",
  "hsl(var(--chart-7))",
  "hsl(var(--chart-8))",
];

function CustomTooltip({
  active,
  payload,
  total,
  currency,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; payload?: { fill?: string } }>;
  total: number;
  currency: string;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0];
  const value = d.value ?? 0;
  const pct = total > 0 ? ((value / total) * 100).toFixed(1) : "0";
  return (
    <div className="rounded-xl border border-border bg-popover/95 px-3 py-2 shadow-lg backdrop-blur-sm">
      <p className="text-xs font-medium text-muted-foreground">{d.name}</p>
      <p className="text-sm font-semibold tabular-nums" style={{ color: d.payload?.fill }}>
        {currency}
        {value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        <span className="ml-1 text-xs font-normal text-muted-foreground">({pct}%)</span>
      </p>
    </div>
  );
}

export function CategoryChart({ data, currency }: CategoryChartProps) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const hasData = data.length > 0 && total > 0;

  return (
    <Card className="shadow-card animate-fade-in overflow-hidden" style={{ animationDelay: "300ms" }}>
      <CardHeader className="border-b border-border/60 bg-muted/20 pb-3">
        <CardTitle className="text-base font-semibold tracking-tight">Category breakdown</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">Where your spending went this period</p>
      </CardHeader>
      <CardContent className="pt-4">
        {!hasData ? (
          <div className="flex h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 px-6 text-center">
            <p className="text-sm font-medium text-foreground">No categories yet</p>
            <p className="mt-1 max-w-xs text-xs text-muted-foreground">
              After you upload a statement, spending by category appears here as a pie chart.
            </p>
          </div>
        ) : (
          <div className="relative h-[320px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="46%"
                  innerRadius="52%"
                  outerRadius="78%"
                  paddingAngle={2}
                  stroke="hsl(var(--background))"
                  strokeWidth={2}
                  animationDuration={800}
                  onMouseEnter={(_, i) => setActiveIndex(i)}
                  onMouseLeave={() => setActiveIndex(null)}
                >
                  {data.map((_, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={COLORS[index % COLORS.length]}
                      opacity={activeIndex === null || activeIndex === index ? 1 : 0.45}
                      style={{ transition: "opacity 0.2s ease" }}
                    />
                  ))}
                </Pie>
                <Tooltip content={<CustomTooltip total={total} currency={currency} />} />
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute left-1/2 top-[42%] w-[120px] -translate-x-1/2 -translate-y-1/2 text-center">
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Total</p>
              <p className="text-lg font-bold tabular-nums leading-tight text-foreground">
                {currency}
                {total.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="absolute bottom-1 left-0 right-0 flex flex-wrap justify-center gap-x-3 gap-y-1 px-2">
              {data.slice(0, 8).map((d, i) => (
                <span key={d.name} className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: COLORS[i % COLORS.length] }}
                  />
                  <span className="max-w-[88px] truncate">{d.name}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

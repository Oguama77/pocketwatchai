import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SpendingPoint } from "@/types/analytics";

interface SpendingChartProps {
  data: SpendingPoint[];
  currency: string;
}

function CustomTooltip({
  active,
  payload,
  label,
  currency,
}: {
  active?: boolean;
  payload?: Array<{ value: number }>;
  label?: string;
  currency: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-border bg-popover/95 px-3 py-2 shadow-lg backdrop-blur-sm">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums text-foreground">
        {currency}{" "}
        {payload[0].value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
      </p>
    </div>
  );
}

export function SpendingChart({ data, currency }: SpendingChartProps) {
  const hasData = data.length > 0;

  return (
    <Card className="shadow-card animate-fade-in overflow-hidden" style={{ animationDelay: "200ms" }}>
      <CardHeader className="border-b border-border/60 bg-muted/20 pb-3">
        <CardTitle className="text-base font-semibold tracking-tight">Spending over time</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">Monthly expenses from your statement</p>
      </CardHeader>
      <CardContent className="pt-4">
        {!hasData ? (
          <div className="flex h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 px-6 text-center">
            <p className="text-sm font-medium text-foreground">No spending data yet</p>
            <p className="mt-1 max-w-xs text-xs text-muted-foreground">
              Upload a bank or card statement to see your spending trend as a bar chart.
            </p>
          </div>
        ) : (
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 12, right: 8, left: 4, bottom: 8 }} barCategoryGap="18%">
                <defs>
                  <linearGradient id="spendingBarFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={1} />
                    <stop offset="100%" stopColor="hsl(var(--chart-2))" stopOpacity={0.75} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="4 8" stroke="hsl(var(--border))" vertical={false} strokeOpacity={0.6} />
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                  tickMargin={10}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                  tickFormatter={(v) =>
                    Math.abs(v) >= 1000 ? `${currency}${Math.round(v / 1000)}k` : `${currency}${Math.round(v)}`
                  }
                />
                <Tooltip
                  cursor={{ fill: "hsl(var(--accent))", opacity: 0.35, radius: 6 }}
                  content={<CustomTooltip currency={currency} />}
                />
                <Bar
                  dataKey="amount"
                  fill="url(#spendingBarFill)"
                  radius={[8, 8, 4, 4]}
                  maxBarSize={48}
                  animationDuration={900}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SpendingPoint } from "@/types/analytics";

interface SpendingChartProps {
  data: SpendingPoint[];
  currency: string;
}

const CustomTooltip = ({ active, payload, label, currency }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-card">
      <p className="text-sm font-medium">{label}</p>
      <p className="text-sm text-primary font-semibold">
        {currency} {payload[0].value.toLocaleString()}
      </p>
    </div>
  );
};

export function SpendingChart({ data, currency }: SpendingChartProps) {
  return (
    <Card className="shadow-card animate-fade-in" style={{ animationDelay: "200ms" }}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold">Spending Over Time</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} tickFormatter={(v) => `${currency} ${Math.round(v / 1000)}k`} />
              <Tooltip content={<CustomTooltip currency={currency} />} cursor={{ fill: "hsl(var(--accent))" }} />
              <Bar dataKey="amount" fill="hsl(var(--chart-1))" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

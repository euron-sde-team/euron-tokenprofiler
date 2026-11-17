import { cn } from "@/lib/cn";

const CONF: Record<string, string> = { high: "border-completed/30 bg-completed/10 text-completed", medium: "border-waiting/30 bg-waiting/10 text-waiting", low: "border-line bg-raised text-muted" };
export function Confidence({ level }: { level: "high" | "medium" | "low" }) {
  return <span data-confidence={level} className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium capitalize", CONF[level])}>{level} confidence</span>;
}
export function Effort({ level }: { level: "high" | "medium" | "low" }) {
  return <span className="rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted">{level} effort</span>;
}
export function Chip({ children, tone = "text-muted" }: { children: React.ReactNode; tone?: string }) {
  return <span className={cn("rounded-md border border-line px-1.5 py-0.5 text-[11px]", tone)}>{children}</span>;
}
const STATUS: Record<string, { label: string; tone: string }> = {
  ok: { label: "On track", tone: "text-completed" }, warning: { label: "Warning", tone: "text-waiting" },
  projected_overrun: { label: "Projected to overrun", tone: "text-waiting" }, exceeded: { label: "Exceeded", tone: "text-failed" },
};
export function BudgetBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.ok!;
  return <span data-status={status} className={cn("text-xs font-medium", s.tone)}>{s.label}</span>;
}
export const budgetTone = (status: string) => (status === "exceeded" ? "bg-failed" : status === "ok" ? "bg-completed" : "bg-waiting");

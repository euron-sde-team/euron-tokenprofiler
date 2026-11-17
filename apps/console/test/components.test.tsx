import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HBars } from "@/components/charts/hbars";
import { StackedBars } from "@/components/charts/stacked-bars";
import { BudgetBadge, Confidence, budgetTone } from "@/components/ui/badges";
import { snippets } from "@/lib/snippets";

const rows = [
  { day: 1, model: "gpt-4o", cost: 3_000_000 }, { day: 1, model: "other", cost: 1_000_000 },
  { day: 2, model: "gpt-4o", cost: 2_000_000 },
];

describe("StackedBars", () => {
  it("renders one column per day, a legend with every series, and the day's breakdown on hover", () => {
    render(<StackedBars rows={rows} by="model" />);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "cost by model over 2 days");
    expect(screen.getAllByText("gpt-4o").length).toBeGreaterThan(0);
    const column = screen.getByRole("img").firstElementChild!;
    fireEvent.mouseEnter(column);
    expect(screen.getByText("$4.00 total")).toBeInTheDocument();
  });
  it("copes with no data", () => {
    render(<StackedBars rows={[]} by="model" />);
    expect(screen.getByTestId("stacked-bars")).toBeInTheDocument();
  });
});
describe("HBars", () => {
  it("shows a ranked list and an empty message", () => {
    const { rerender } = render(<HBars rows={[{ label: "a", value: 10, display: "$10" }, { label: "b", value: 5, display: "$5", hint: "half" }]} />);
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("half")).toBeInTheDocument();
    rerender(<HBars rows={[]} empty="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});
describe("badges", () => {
  it("labels budget status and picks a tone", () => {
    render(<><BudgetBadge status="exceeded" /><Confidence level="high" /></>);
    expect(screen.getByText("Exceeded")).toHaveAttribute("data-status", "exceeded");
    expect(screen.getByText("high confidence")).toHaveAttribute("data-confidence", "high");
    expect(budgetTone("exceeded")).toBe("bg-failed");
    expect(budgetTone("ok")).toBe("bg-completed");
    expect(budgetTone("warning")).toBe("bg-waiting");
  });
});
describe("integration snippets", () => {
  it("point at this server and mention the right headers", () => {
    const all = snippets("http://localhost:8380");
    expect(all.map((s) => s.id)).toEqual(["sdk", "proxy", "http"]);
    expect(all[1]!.code).toContain("http://localhost:8380/proxy/v1");
    expect(all[1]!.code).toContain("X-Upstream-Api-Key");
    for (const s of all) expect(s.code).toContain("http://localhost:8380");
  });
});

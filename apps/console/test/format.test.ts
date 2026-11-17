import { describe, expect, it } from "vitest";
import { compact, dayLabel, formatDelta, formatDuration, formatPct, formatUsd } from "@/lib/format";

describe("formatUsd", () => {
  it("keeps four decimals for fractions of a cent and abbreviates large sums", () => {
    expect([null, 0, 5, 4_200, 12_400_000, 1_234_000_000].map(formatUsd)).toEqual(["-", "$0.00", "<$0.0001", "$0.0042", "$12.40", "$1.2k"]);
  });
});
describe("compact", () => {
  it("abbreviates token counts", () => {
    expect([950, 12_300, 4_500_000, 2_000_000_000, null].map(compact)).toEqual(["950", "12.3k", "4.5M", "2.0B", "-"]);
  });
});
describe("formatDelta", () => {
  it("signs changes and calls a missing baseline new", () => {
    expect([0.123, -0.045, 1.5, null].map(formatDelta)).toEqual(["+12%", "-4.5%", "+150%", "new"]);
  });
});
describe("misc", () => {
  it("formats percentages, durations and days", () => {
    expect(formatPct(0.256)).toBe("26%");
    expect(formatDuration(1500)).toBe("1.5 s");
    expect(dayLabel(20_000)).toMatch(/^[A-Z][a-z]{2} \d+$/);
  });
});

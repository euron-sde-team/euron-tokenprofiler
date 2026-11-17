import { beforeEach, describe, expect, it } from "vitest";
import { clearConnection, loadConnection, normalizeApiUrl, saveConnection } from "@/lib/connection";

beforeEach(() => window.localStorage.clear());
describe("connection storage", () => {
  it("round-trips and clears", () => {
    expect(loadConnection()).toBeNull();
    saveConnection({ apiUrl: "http://localhost:8380", apiKey: "tp_a_b" });
    expect(loadConnection()).toEqual({ apiUrl: "http://localhost:8380", apiKey: "tp_a_b" });
    clearConnection();
    expect(loadConnection()).toBeNull();
  });
  it("treats corrupt or incomplete values as disconnected, and trims the URL", () => {
    window.localStorage.setItem("tokenprofiler.connection", "{nope");
    expect(loadConnection()).toBeNull();
    expect(normalizeApiUrl("  http://x:1//  ")).toBe("http://x:1");
  });
});

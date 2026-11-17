import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, TokenProfilerApi, filterQuery, qs } from "@/lib/api";

const conn = { apiUrl: "http://api.test", apiKey: "tp_k_secret" };
afterEach(() => vi.unstubAllGlobals());

describe("qs", () => {
  it("skips empty values and repeats array parameters", () => {
    expect(qs({ a: 1, b: undefined, c: "", d: null, e: ["x", "y"], f: false })).toBe("?a=1&e=x&e=y&f=false");
    expect(qs({})).toBe("");
  });
});
describe("filterQuery", () => {
  it("maps the console filter to API parameters", () => {
    expect(filterQuery({ range: "7d", app: "bot", model: undefined })).toMatchObject({ range: "7d", app: "bot" });
  });
});
describe("requests", () => {
  it("sends the key as a bearer token and builds the URL from the filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await new TokenProfilerApi(conn).overview({ range: "30d", app: "support-bot" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/api/overview?range=30d&app=support-bot");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tp_k_secret");
  });
  it("repeats group_by and metrics", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await new TokenProfilerApi(conn).summary({ range: "7d" }, ["app", "model"], ["cost", "requests"]);
    expect(fetchMock.mock.calls[0]![0]).toContain("group_by=app&group_by=model&metrics=cost&metrics=requests");
  });
  it("turns the API error shape into an ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: "bad scope", code: "forbidden", request_id: "r9" } }), { status: 403 })));
    const err = await new TokenProfilerApi(conn).keys().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, code: "forbidden", message: "bad scope", requestId: "r9" });
  });
  it("reports an unreachable server clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(new TokenProfilerApi(conn).system()).rejects.toMatchObject({ status: 0, code: "network", message: "Cannot reach the API at http://api.test" });
  });
  it("handles 204 and encodes prompt ids that contain slashes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(new TokenProfilerApi(conn).dismiss("abc")).resolves.toBeUndefined();
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await new TokenProfilerApi(conn).prompt("team/faq v2", { range: "30d" });
    expect(fetchMock.mock.calls[1]![0]).toContain("/api/prompts/team/faq%20v2?");
  });
});

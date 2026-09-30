import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError, get, getOptional, post } from "./client";

/**
 * The console has to tell genuinely different failures apart: a 404 on a
 * recommendation is a normal state, a 409 on a review means someone decided
 * first, and a network failure means a backend is down. Collapsing them would
 * make the UI useless exactly when an operator needs it.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("get", () => {
  it("returns the parsed body on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { total: 3 })));

    await expect(get<{ total: number }>("/api/investigation/investigations")).resolves.toEqual({
      total: 3,
    });
  });

  it("raises ApiError carrying the status and FastAPI detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Investigation INV-9 was not found." })),
    );

    const error = await get("/x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.isNotFound).toBe(true);
    expect(apiError.detail).toContain("was not found");
  });

  it("reads the Financial Core's message field too", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { message: "Unknown transaction" })));

    const error = (await get("/x").catch((caught: unknown) => caught)) as ApiError;
    expect(error.detail).toBe("Unknown transaction");
  });

  it("flags a 409 as a conflict, distinctly from other failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, { detail: "Investigation INV-1003 is ESCALATED, not AWAITING_REVIEW." }),
      ),
    );

    const error = (await get("/x").catch((caught: unknown) => caught)) as ApiError;
    expect(error.isConflict).toBe(true);
    expect(error.isNotFound).toBe(false);
  });

  it("raises NetworkError when a backend cannot be reached at all", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await get("/x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(NetworkError);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it("does not throw on a non-JSON error body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 })),
    );

    const error = await get("/x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
  });

  // A CloudFront custom_error_response is distribution-wide and once rewrote a
  // genuine API 404 into the SPA shell with a 200. The raw SyntaxError that
  // produced ("Unexpected token '<'") named neither the URL nor the real
  // status, so the console failed in a way nobody could diagnose from it.
  it("rejects an HTML document served with a 200 instead of parsing it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html><body>shell</body></html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );

    const error = await get("/api/investigation/investigations/INV-1002/recommendation").catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.message).toContain("Expected JSON");
    expect(apiError.message).toContain("text/html");
    expect(apiError.url).toContain("INV-1002");
    // Not a SyntaxError from JSON.parse, which is what this replaces.
    expect(error).not.toBeInstanceOf(SyntaxError);
  });

  it("accepts a charset-qualified JSON content type", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json; charset=utf-8" },
        }),
      ),
    );

    await expect(get<{ ok: boolean }>("/x")).resolves.toEqual({ ok: true });
  });
});

describe("getOptional", () => {
  it("returns null on 404, because absent is a normal state", async () => {
    // A PENDING investigation genuinely has no recommendation.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "no recommendation yet" })));

    await expect(getOptional("/recommendation")).resolves.toBeNull();
  });

  it("still propagates every other failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(500, { detail: "boom" })));

    await expect(getOptional("/recommendation")).rejects.toBeInstanceOf(ApiError);
  });

  it("propagates a network failure rather than hiding it as absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(getOptional("/recommendation")).rejects.toBeInstanceOf(NetworkError);
  });

  // "Absent" means the API said 404. An HTML page with a 200 means the request
  // never reached the API, which is a routing fault and must stay visible —
  // softening it to null would render "no recommendation" over a broken CDN.
  it("does not treat a misrouted HTML 200 as an absent resource", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );

    await expect(getOptional("/recommendation")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("post", () => {
  it("sends JSON and returns the parsed response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { decision: "APPROVED" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(post("/approve", { reviewed_by: "ops.analyst" })).resolves.toEqual({
      decision: "APPROVED",
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reviewed_by: "ops.analyst" });
  });
});

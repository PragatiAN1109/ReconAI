import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Routing tests for the CloudFront viewer-request function.
 *
 * This reads and evaluates the exact file Terraform deploys
 * (`aws_cloudfront_function.viewer_request` does `file(...)` on the same path),
 * so the behaviour asserted here is the behaviour that ships.
 *
 * It lives here, outside src/, because it is not frontend source: src/ is what
 * tsconfig typechecks and what ships in the bundle. It runs in the frontend
 * suite only because that is where a JavaScript runner already exists — adding
 * one under infra/ would mean a new toolchain for a single 40-line file.
 *
 * What it guards: this function is now the only thing keeping API responses and
 * the SPA shell apart. A distribution-wide `custom_error_response` used to map
 * 403/404 -> /index.html with a 200, which also rewrote genuine API errors —
 * a correct 404 from a FAILED investigation's missing recommendation reached
 * the browser as HTML and broke JSON parsing. That rule is gone; these
 * assertions are what stop it coming back in another form.
 */

const FUNCTION_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../infra/functions/viewer-request.js",
);

type CloudFrontRequest = { uri: string; querystring?: Record<string, unknown> };

function loadHandler(): (event: { request: CloudFrontRequest }) => CloudFrontRequest {
  const source = readFileSync(FUNCTION_PATH, "utf-8");
  // The file declares `function handler(event)` at top level, the shape
  // CloudFront requires. Evaluate it and hand the function back.
  return new Function(`${source}\nreturn handler;`)() as ReturnType<typeof loadHandler>;
}

const handler = loadHandler();

function route(uri: string): string {
  return handler({ request: { uri } }).uri;
}

describe("API prefix rewriting", () => {
  it("collapses /api/core to /api/v1", () => {
    expect(route("/api/core/exceptions")).toBe("/api/v1/exceptions");
    expect(route("/api/core/transactions/TX-10003")).toBe("/api/v1/transactions/TX-10003");
  });

  it("collapses /api/investigation to /api/v1", () => {
    expect(route("/api/investigation/investigations")).toBe("/api/v1/investigations");
    expect(route("/api/investigation/investigations/INV-1003/approve")).toBe(
      "/api/v1/investigations/INV-1003/approve",
    );
  });

  it("leaves the rest of the path, including nested segments, intact", () => {
    expect(route("/api/investigation/investigations/INV-1002/recommendation")).toBe(
      "/api/v1/investigations/INV-1002/recommendation",
    );
    expect(route("/api/investigation/investigations/INV-1002/audit")).toBe(
      "/api/v1/investigations/INV-1002/audit",
    );
  });
});

describe("API paths are never replaced by the SPA shell", () => {
  // The regression that motivated this file. INV-1002 is FAILED and has no
  // recommendation, so the API answers 404 — and that 404 has to survive the
  // edge as itself rather than arriving as index.html with a 200.
  it("does not rewrite the recommendation path of a FAILED investigation", () => {
    const uri = route("/api/investigation/investigations/INV-1002/recommendation");
    expect(uri).not.toBe("/index.html");
    expect(uri).toBe("/api/v1/investigations/INV-1002/recommendation");
  });

  it("never routes any /api/ path to the shell, extensionless or not", () => {
    const paths = [
      "/api/core/exceptions",
      "/api/core/transactions/TX-10003/settlements",
      "/api/investigation/investigations",
      "/api/investigation/investigations/INV-1002/recommendation",
      "/api/investigation/investigations/INV-1002/audit",
      "/api/investigation/investigations/INV-1003/reject",
      // Unrecognised /api/ paths still must not become the shell.
      "/api/something-else/deep/path",
      "/api/investigation",
    ];

    for (const path of paths) {
      expect(route(path)).not.toBe("/index.html");
    }
  });
});

describe("SPA history fallback", () => {
  it("serves the shell for deep client-side routes on direct navigation", () => {
    expect(route("/investigations/INV-1002")).toBe("/index.html");
    expect(route("/investigations/INV-1003")).toBe("/index.html");
  });

  it("serves the shell for the root and for unknown extensionless routes", () => {
    expect(route("/")).toBe("/index.html");
    expect(route("/dashboard")).toBe("/index.html");
    expect(route("/does/not/exist")).toBe("/index.html");
  });

  it("leaves real static assets alone so a missing one fails as a missing asset", () => {
    const assets = [
      "/assets/index-BSjRzyLE.js",
      "/assets/index-abc123.css",
      "/favicon.ico",
      "/index.html",
      "/assets/index-BSjRzyLE.js.map",
    ];

    for (const asset of assets) {
      expect(route(asset)).toBe(asset);
    }
  });
});

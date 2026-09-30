import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * What the CloudFront distribution allows, read from the Terraform source.
 *
 * A static assertion rather than a deployed check, because the property being
 * protected is a decision in the configuration: exactly one path under
 * /api/core is publicly writable, and widening that is the mistake this file
 * exists to catch. `terraform validate` will not notice a broadened
 * allowed_methods, and a reviewer reading a large plan diff might not either.
 *
 * It lives outside src/ for the same reason as viewerRequest.test.ts — it is not
 * frontend source, and only runs here because a JS runner already exists.
 */

const TERRAFORM = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../../infra/frontend.tf"),
  "utf-8",
);

/** The seven-method list CloudFront requires in order to permit any write. */
const WRITE_METHODS = /"GET",\s*"HEAD",\s*"OPTIONS",\s*"PUT",\s*"POST",\s*"PATCH",\s*"DELETE"/;

interface Behaviour {
  pathPattern: string;
  allowedMethods: string;
  index: number;
}

/** Parses each ordered_cache_behavior's path pattern and method list, in order. */
function orderedBehaviours(): Behaviour[] {
  const behaviours: Behaviour[] = [];
  const blockStart = /ordered_cache_behavior\s*\{/g;

  let match: RegExpExecArray | null;
  while ((match = blockStart.exec(TERRAFORM)) !== null) {
    const body = TERRAFORM.slice(match.index, match.index + 1200);
    const pathPattern = /path_pattern\s*=\s*"([^"]+)"/.exec(body);
    const allowedMethods = /allowed_methods\s*=\s*\[([^\]]+)\]/.exec(body);
    if (pathPattern && allowedMethods) {
      behaviours.push({
        pathPattern: pathPattern[1],
        allowedMethods: allowedMethods[1],
        index: match.index,
      });
    }
  }
  return behaviours;
}

describe("only the intended demo route is publicly writable", () => {
  it("permits writes on exactly one path", () => {
    const writable = orderedBehaviours().filter((behaviour) =>
      WRITE_METHODS.test(behaviour.allowedMethods),
    );

    expect(writable.map((behaviour) => behaviour.pathPattern).sort()).toEqual([
      "/api/core/demo/reconcile",
      "/api/investigation/*",
    ]);
  });

  it("keeps the general Financial Core prefix read-only", () => {
    // This is what stops POST /api/core/transactions, POST /api/core/settlements
    // and POST /api/core/reconciliation/* from reaching the internet.
    const core = orderedBehaviours().find(
      (behaviour) => behaviour.pathPattern === "/api/core/*",
    );

    expect(core).toBeDefined();
    expect(core!.allowedMethods).toMatch(/"GET",\s*"HEAD"/);
    expect(core!.allowedMethods).not.toMatch(/POST/);
    expect(core!.allowedMethods).not.toMatch(/PUT|PATCH|DELETE/);
  });

  it("names the demo route exactly, never as a wildcard", () => {
    // /api/core/demo/* would make every future path under it writable by
    // accident. CloudFront cannot narrow the method list, so the path is the
    // only thing bounding this.
    const patterns = orderedBehaviours().map((behaviour) => behaviour.pathPattern);

    expect(patterns).toContain("/api/core/demo/reconcile");
    expect(patterns).not.toContain("/api/core/demo/*");
  });

  it("declares the demo route before the read-only prefix that would shadow it", () => {
    // CloudFront takes the first matching behaviour. Reversed, /api/core/* wins
    // and the POST is rejected at the edge.
    const behaviours = orderedBehaviours();
    const demo = behaviours.find((b) => b.pathPattern === "/api/core/demo/reconcile");
    const core = behaviours.find((b) => b.pathPattern === "/api/core/*");

    expect(demo).toBeDefined();
    expect(core).toBeDefined();
    expect(demo!.index).toBeLessThan(core!.index);
  });

  it("serves the SPA itself read-only", () => {
    const defaultBehaviour = /default_cache_behavior\s*\{[\s\S]{0,600}?allowed_methods\s*=\s*\[([^\]]+)\]/.exec(
      TERRAFORM,
    );

    expect(defaultBehaviour).not.toBeNull();
    expect(defaultBehaviour![1]).not.toMatch(/POST|PUT|PATCH|DELETE/);
  });
});

describe("the routing fixes this distribution already carries", () => {
  it("declares no custom_error_response", () => {
    // A distribution-wide error page once rewrote API 404s into the SPA shell
    // with a 200. SPA fallback lives in the viewer-request function instead.
    expect(/^\s*custom_error_response\s*\{/m.test(TERRAFORM)).toBe(false);
  });

  it("still requires the origin-verify secret on the API behaviours", () => {
    // The ALB is internet-facing; this header is what stops a caller bypassing
    // CloudFront and its method restrictions entirely.
    expect(TERRAFORM).toMatch(/X-Origin-Verify/i);
  });

  it("has not reintroduced basic auth", () => {
    expect(TERRAFORM).not.toMatch(/basic_auth/i);
  });
});

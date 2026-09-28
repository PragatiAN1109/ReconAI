/**
 * The single place HTTP happens.
 *
 * Errors are typed rather than thrown as bare strings, because the UI has to
 * tell genuinely different situations apart: a 404 on a recommendation means
 * "no conclusion yet" and is normal, a 409 on a review means someone else
 * decided first, and a network failure means a backend is down. Collapsing
 * those into one "something went wrong" would make the console useless exactly
 * when an operator needs it.
 */

/** Prefixes resolved by the dev-server proxy. See vite.config.ts. */
export const CORE_BASE = "/api/core";
export const INVESTIGATION_BASE = "/api/investigation";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly url: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }

  /** The resource does not exist — often a normal state, not a failure. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The request was valid but the resource's state forbids it. */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

/** A backend could not be reached at all, as distinct from refusing. */
export class NetworkError extends Error {
  constructor(readonly url: string, cause?: unknown) {
    super(
      "Could not reach the service. Check that the backend is running and that " +
        "the dev server proxy is configured.",
    );
    this.name = "NetworkError";
    this.cause = cause;
  }
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    // FastAPI uses `detail`; the Financial Core's error envelope uses `message`.
    return body?.detail ?? body?.message ?? response.statusText;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    throw new NetworkError(url, cause);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response), url);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function get<T>(url: string): Promise<T> {
  return request<T>(url);
}

export function post<T>(url: string, body?: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * Resolve to null on 404 instead of throwing.
 *
 * For several resources here "absent" is an expected state rather than an
 * error: a PENDING investigation genuinely has no recommendation, and the
 * backend says so with a 404 by design. Only 404 is softened — every other
 * failure still propagates.
 */
export async function getOptional<T>(url: string): Promise<T | null> {
  try {
    return await get<T>(url);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) {
      return null;
    }
    throw error;
  }
}

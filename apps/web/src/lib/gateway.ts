/**
 * Transport. Every byte this console exchanges with the backend goes through here.
 *
 * This module is the *only* place `NEXT_PUBLIC_GATEWAY_URL` is read, and it is the only
 * backend address the application holds -- an architectural invariant, not a
 * convenience. `adjudication`, `auth`, `policy` and `evals` are not addressable from the
 * browser at all; the gateway is the single front door, so authentication, role gating
 * and rate limiting cannot be bypassed by a client that knows a service's port. A `grep`
 * for `http` outside this file should find nothing.
 *
 * There is deliberately no fallback address. `NEXT_PUBLIC_*` values are inlined at build
 * time, so a default here would be compiled into the image and a misconfigured build
 * would fail by quietly talking to the wrong host instead of saying so.
 */

/** A non-2xx answer from the gateway, carrying the status the caller has to branch on. */
export class GatewayError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "GatewayError";
  }

  /** A session that is absent, unknown or expired -- the three the gateway does not
   * distinguish. Callers use this to send the user back to the login screen rather
   * than rendering an error over a page they can no longer load. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** A real session whose role does not satisfy the route. Not a login problem: signing
   * in again would produce the same answer. */
  get isForbidden(): boolean {
    return this.status === 403;
  }
}

function gatewayUrl(): string {
  const url = process.env.NEXT_PUBLIC_GATEWAY_URL;
  if (!url) {
    throw new GatewayError(
      0,
      "NEXT_PUBLIC_GATEWAY_URL is not set; this build has no backend address",
    );
  }
  return url.replace(/\/$/, "");
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  /** Omitted for the two public routes: logging in, and liveness. */
  token?: string;
  signal?: AbortSignal;
}

/**
 * `detail` is FastAPI's own error shape. Falling back to the status line matters: the
 * gateway's 429 and 503 carry a detail, but a response that never reached FastAPI (a
 * dead upstream, a CORS rejection) has no JSON body at all, and "Failed to fetch" in
 * front of a reviewer is less use than the status.
 */
async function errorFrom(response: Response): Promise<GatewayError> {
  let detail = `${response.status} ${response.statusText}`.trim();
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) {
      detail = body.detail;
    }
  } catch {
    // Not JSON. The status line stands.
  }
  return new GatewayError(response.status, detail);
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token, signal } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // A bearer header rather than a cookie: the console and the gateway are separate
  // origins over plain HTTP in development, where a cross-site cookie would need
  // `SameSite=None; Secure` and so would never be sent. The gateway accepts either.
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${gatewayUrl()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new GatewayError(0, `the gateway did not answer (${String(cause)})`);
  }

  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Read a Server-Sent Events response, calling `onMessage` with each frame's data.
 *
 * Written over `fetch` rather than `EventSource` because `EventSource` cannot set
 * request headers, and every route behind this gateway requires a session. The
 * alternative -- putting the token in the query string -- would write a live credential
 * into every access log between here and the service. So the stream is read as a
 * `ReadableStream` and framed here.
 *
 * Framing follows the SSE grammar rather than assuming one `data:` line per frame:
 * events are separated by a blank line, and a frame's data is the concatenation of its
 * `data:` lines. `adjudication` emits exactly one line per frame today; a parser that
 * depended on that would break silently the day a payload contained a newline.
 *
 * Returns when the stream ends. Cancel it with `signal`.
 */
export async function readEventStream(
  path: string,
  token: string,
  onMessage: (data: string) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${gatewayUrl()}${path}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    signal,
  });

  if (!response.ok) throw await errorFrom(response);
  if (!response.body) throw new GatewayError(0, "the gateway returned no stream body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // A frame is complete only once its terminating blank line has arrived; anything
      // after the last one is a partial frame and stays in the buffer for the next read.
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) onMessage(data);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    // Releasing the lock lets the abort actually tear the connection down; without it a
    // navigated-away-from case would hold its subscription open until the tab closed.
    reader.releaseLock();
  }
}

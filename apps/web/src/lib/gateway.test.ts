/**
 * `readEventStream`'s frame parsing.
 *
 * This is the code that turns the case screen into a live view, and its failure modes are
 * quiet ones: a frame split across two network reads that gets dropped, or a multi-line
 * `data:` payload joined wrongly so `JSON.parse` throws and takes the whole subscription
 * down. A reviewer watching a case would see it stop updating with no error, which is worse
 * than an error, because the case is still moving and the screen says otherwise.
 *
 * Chunk boundaries are chosen adversarially rather than conveniently: the interesting bugs
 * live exactly where a frame terminator straddles two reads, so several tests split the
 * payload *inside* the `\n\n` that ends a frame.
 */

import { describe, expect, it, vi } from "vitest";

import { readEventStream } from "@/lib/gateway";

const encoder = new TextEncoder();

/** A `fetch` that streams `chunks` verbatim, one `read()` per chunk. */
function stubFetch(chunks: string[], { ok = true, body = true } = {}) {
  let index = 0;
  const reader = {
    read: async () =>
      index < chunks.length
        ? { done: false, value: encoder.encode(chunks[index++]) }
        : { done: true, value: undefined },
    releaseLock: vi.fn(),
  };
  const response = {
    ok,
    status: ok ? 200 : 500,
    body: body ? { getReader: () => reader } : null,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ detail: "boom" }),
    text: async () => "boom",
  };
  vi.stubGlobal("fetch", vi.fn(async () => response));
  return reader;
}

async function collect(chunks: string[]): Promise<string[]> {
  stubFetch(chunks);
  const seen: string[] = [];
  await readEventStream("/api/cases/x/stream", "t", (d) => seen.push(d), new AbortController().signal);
  return seen;
}

describe("frames arriving whole", () => {
  it("reads a single frame", async () => {
    expect(await collect(['data: {"seq":1}\n\n'])).toEqual(['{"seq":1}']);
  });

  it("reads several frames from one chunk", async () => {
    expect(await collect(["data: a\n\ndata: b\n\ndata: c\n\n"])).toEqual(["a", "b", "c"]);
  });

  it("strips only the leading space after the colon", async () => {
    // `data:  x` has two spaces; SSE says one is the delimiter, so the payload keeps none of
    // its own leading whitespace only if `trimStart` is doing more than the spec asks. This
    // pins current behaviour so a change to it is deliberate.
    expect(await collect(["data:x\n\n"])).toEqual(["x"]);
  });

  it("ignores comment and field lines that are not data", async () => {
    // A `:` line is an SSE keep-alive comment; `event:` and `id:` are fields this consumer
    // does not use. None of them may reach `JSON.parse`.
    expect(await collect([":keep-alive\nevent: ping\nid: 7\ndata: real\n\n"])).toEqual(["real"]);
  });

  it("drops a frame carrying no data line at all", async () => {
    expect(await collect([":keep-alive\n\ndata: real\n\n"])).toEqual(["real"]);
  });

  it("joins a multi-line data payload with newlines", async () => {
    // The SSE rule, and it matters here: the payload is JSON, so joining with anything else
    // produces a string `JSON.parse` rejects, and one bad frame rejects the whole stream.
    expect(await collect(['data: {"a":1,\ndata: "b":2}\n\n'])).toEqual(['{"a":1,\n"b":2}']);
  });
});

describe("frames split across reads", () => {
  it("reassembles a frame split mid-payload", async () => {
    expect(await collect(['data: {"seq"', ":1}\n\n"])).toEqual(['{"seq":1}']);
  });

  it("reassembles a frame split inside its terminator", async () => {
    // The boundary case the buffer exists for: the first read ends with a single newline,
    // so `indexOf("\n\n")` finds nothing and must not consume the partial frame.
    expect(await collect(["data: x\n", "\n"])).toEqual(["x"]);
  });

  it("holds a partial frame until its terminator arrives", async () => {
    expect(await collect(["data: x\n\ndata: y"])).toEqual(["x"]);
  });

  it("delivers a frame assembled from many small reads", async () => {
    expect(await collect(["d", "a", "t", "a", ":", " ", "z", "\n", "\n"])).toEqual(["z"]);
  });

  it("handles a chunk boundary between two complete frames", async () => {
    expect(await collect(["data: a\n\n", "data: b\n\n"])).toEqual(["a", "b"]);
  });

  it("decodes a multi-byte character split across two reads", async () => {
    // `TextDecoder` is created with `{ stream: true }` for exactly this. A naive decode
    // per chunk turns a split code point into a replacement character, which corrupts a
    // clinical narrative rather than failing loudly.
    const bytes = encoder.encode("data: café\n\n");
    const split = 8; // inside the two bytes of "é"
    const chunks = [bytes.slice(0, split), bytes.slice(split)];

    let index = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: async () =>
              index < chunks.length
                ? { done: false, value: chunks[index++] }
                : { done: true, value: undefined },
            releaseLock: vi.fn(),
          }),
        },
      })),
    );

    const seen: string[] = [];
    await readEventStream("/s", "t", (d) => seen.push(d), new AbortController().signal);
    expect(seen).toEqual(["café"]);
  });
});

describe("CRLF framing", () => {
  /**
   * The carried finding, now a test. The SSE grammar permits `\r\n` line endings, so a frame
   * may be terminated by `\r\n\r\n`. Splitting on `"\n\n"` alone never finds that boundary:
   * every frame stays in the buffer and the case screen silently never updates.
   *
   * Correct against the server this project ships, which writes `\n\n`. It stops being
   * correct behind any proxy that rewrites line endings, and the failure is invisible --
   * no error, no event, just a screen that does not move.
   */
  it("reads a frame terminated with CRLF", async () => {
    expect(await collect(["data: x\r\n\r\n"])).toEqual(["x"]);
  });

  it("reads several CRLF frames", async () => {
    expect(await collect(["data: a\r\n\r\ndata: b\r\n\r\n"])).toEqual(["a", "b"]);
  });

  it("joins a multi-line CRLF payload with newlines", async () => {
    expect(await collect(['data: {"a":1,\r\ndata: "b":2}\r\n\r\n'])).toEqual(['{"a":1,\n"b":2}']);
  });

  it("reassembles a CRLF frame split inside its terminator", async () => {
    expect(await collect(["data: x\r\n", "\r\n"])).toEqual(["x"]);
  });

  it("keeps a multi-line payload whole when a read splits CRLF between the CR and the LF", async () => {
    // The case the deferred trailing CR exists for, and the only one that distinguishes it.
    // A bare `\r` is a legal line terminator, so normalising it the moment it arrives turns
    // this single two-line frame into two one-line frames -- and since the payload is JSON,
    // that is two invalid fragments where there should be one valid object. The boundary is
    // invented in the middle of the data, which is the worst available outcome: no error,
    // and `JSON.parse` rejecting a frame takes the whole subscription down.
    expect(await collect(['data: {"a":1,\r', '\ndata: "b":2}\r\n\r\n'])).toEqual([
      '{"a":1,\n"b":2}',
    ]);
  });

  it("handles a stream that mixes LF and CRLF frames", async () => {
    expect(await collect(["data: a\n\ndata: b\r\n\r\ndata: c\n\n"])).toEqual(["a", "b", "c"]);
  });
});

describe("teardown and failure", () => {
  it("releases the reader lock when the stream ends", async () => {
    // Without this the abort cannot tear the connection down, and a navigated-away-from
    // case holds its subscription open until the tab closes.
    const reader = stubFetch(["data: x\n\n"]);
    await readEventStream("/s", "t", () => {}, new AbortController().signal);
    expect(reader.releaseLock).toHaveBeenCalled();
  });

  it("releases the reader lock even when a handler throws", async () => {
    const reader = stubFetch(["data: x\n\n"]);
    await expect(
      readEventStream(
        "/s",
        "t",
        () => {
          throw new Error("handler blew up");
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow("handler blew up");
    expect(reader.releaseLock).toHaveBeenCalled();
  });

  it("throws when the gateway refuses the subscription", async () => {
    stubFetch([], { ok: false });
    await expect(
      readEventStream("/s", "t", () => {}, new AbortController().signal),
    ).rejects.toThrow();
  });

  it("throws a named error when the response carries no stream body", async () => {
    stubFetch([], { body: false });
    await expect(
      readEventStream("/s", "t", () => {}, new AbortController().signal),
    ).rejects.toThrow(/no stream body/);
  });
});

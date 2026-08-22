# apps/web — the reviewer console

The screen a clinician uses to pick up a case the automated gate declined to approve, read
the evidence it assembled, watch it work, and record their own decision.

## The one address

This application holds `NEXT_PUBLIC_GATEWAY_URL` and no other backend address, and it is
read in exactly one place: `src/lib/gateway.ts`. `adjudication`, `auth`, `policy` and
`evals` are not reachable from the browser, so authentication, role gating and rate
limiting cannot be routed around by a client that knows a service's port.

There is no fallback value. `NEXT_PUBLIC_*` is inlined at build time, so a default would
be compiled into the image and a misconfigured build would fail by talking to the wrong
host rather than by saying so.

## Dependencies

Four, and each one is the framework or its types:

| package | why |
| --- | --- |
| `next` | App Router, the build, the production server |
| `react`, `react-dom` | the framework's own runtime |
| `typescript` + `@types/*` | `npm run lint` is `tsc --noEmit`; the wire types in `src/lib/types.ts` are the check that a route change is noticed |

No UI kit, no CSS framework, no data-fetching library, no state library, no ESLint config.
Five screens do not need any of them, and each would be a configuration surface to keep
current. Styling is one hand-written stylesheet (`src/app/globals.css`).

## Layout

```
src/lib/gateway.ts     transport: the URL, fetch, errors, the SSE reader
src/lib/api.ts         one function per gateway route the console calls
src/lib/types.ts       the wire shapes, mirroring what the services return
src/lib/session.ts     where the token lives, and who may record a review
src/components/        small focused pieces; nothing here fetches except the screens
src/app/               login, the queue, the case
```

Every screen renders on the client and fetches with the reviewer's own token. Server
components fetching through the gateway would put the credential on the Next.js server
and make that server a second holder of a backend address.

## Two things the law puts in the UI

**AI use is disclosed where it is encountered.** Utah requires it, so the disclosure is in
the app shell on every screen and again, in specific terms, on the case screen next to the
verdicts it describes — naming which steps a model performed and which were arithmetic.
See `src/components/AiDisclosure.tsx`.

**The machine has no deny path.** It approves or it refers to a clinician (ADR-0002), and
this console never renders a deny affordance for an automated determination — an outcome
it does not recognise is labelled unrecognised rather than given a name. A clinician's own
adverse decision in the review form is a different act, made by a licensed person, and is
legitimate.

## Running it

```bash
cp .env.example .env.local
npm install
npm run dev      # http://localhost:3000
npm run lint     # tsc --noEmit
```

Under compose the gateway address comes from the `web` block instead; `docker compose up
-d --build` builds and serves it on `${WEB_PORT:-3000}`.

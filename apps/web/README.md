# web — the reviewer console

**The surface a clinician actually works.** Port 3000. Next.js 15, React 19, TypeScript.

Every escalated case ends up in front of a person, and this is that person's view: the queue, the
case with its criteria and evidence, the live pipeline as it runs, the form that records their
decision, and — for operators — the eval harness.

## Routes

| route | who | what |
| --- | --- | --- |
| `/login` | anyone | sign in |
| `/cases` | any session | the reviewer queue, filtered by outcome |
| `/cases/new` | any session | case intake |
| `/cases/[caseId]` | any session | the case: criteria, verdicts, evidence, live steps, review form |
| `/evals` | operator | golden cases and eval runs |
| `/evals/runs/[runId]` | operator | a run's report, the threshold sweep, run comparison |

`/` redirects to `/cases`.

## How it talks to the backend

**One address.** The console holds the gateway's URL and no other backend address — a grep for
`http` outside the single gateway module finds nothing. There is no second door for a browser to
find, and no possibility of the console reaching a service directly and asserting its own role.

```
browser ──► /lib/gateway.ts ──► gateway :8000 ──► services
                │
                └── readEventStream() ──► SSE ──► live case view
```

Authorisation is enforced at the gateway, and mirrored on the page. `/evals` checks the role on
the page as well as hiding itself from the nav, because a pasted URL would otherwise render a
screen of 403 errors — which reads as a broken system rather than as the wrong account. A `501`
from any endpoint renders as "not built yet": calm, never an error, and never a zero.

## Design decisions worth knowing

**`OutcomeBadge` renders exactly two outcomes.** `approve` and `escalate` are enumerated, not
derived from the string, and anything else is labelled "unrecognised" and shown verbatim. There
is deliberately **no default branch**, because the one thing this console must never do is put a
denial in front of a reviewer as though the system had issued one. A default that mapped an
unknown value to a plausible label is the shape that failure would take.

Escalate is **amber, never red**. Red reads as refusal; an escalation is the machine handing a
decision to a person, which is the system working as designed.

**Intake mints its own idempotency key**, reused while the form is unchanged and re-minted when
any field changes. So a double-click returns the first case, and an edited resubmission is not
answered with the previous one's determination. The form states the measured retrieval result at
the narrative field, because that is the one input whose absence degrades a determination
silently rather than failing.

**Every money figure is a count times a named rate.** The eval report never shows a bare total —
a number nobody can decompose is a number to be believed rather than checked.

**The threshold sweep is a curve with its minimum marked**, with the full table beneath it. A
flat curve says it is flat rather than pretending to recommend an operating point.

## Running and testing

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
npm test             # 58 unit tests (vitest)
npx tsc --noEmit     # the lint gate for this package
```

`NEXT_PUBLIC_GATEWAY_URL` is required. The gateway module refuses to build a URL without it and
says so loudly, rather than shipping a console that points at nothing.

Tests run in Node, not a browser: the two things under test are a stream parser and a component
rendered to a string with `react-dom/server`. Neither touches a document, so a jsdom environment
would be a dependency bought for nothing.

## Caveats

- **No screen in this console has ever been rendered by a person.** It compiles, it typechecks,
  it has unit tests, and nobody has looked at it in a browser. Treat every screen as unverified —
  in particular the intake form's error paths (bad member id, empty narrative, double submit,
  resubmit after editing one field) and the threshold sweep chart, whose two known defects were
  fixed by reading the code rather than by looking at the output.
- **Unit coverage is two modules deep, not broad.** The SSE frame parser and `OutcomeBadge` are
  well covered because their failure modes are quiet or legal. The pages have no tests.
- **The SSE parser normalises line endings but does not reconnect.** If the stream drops, the live
  view stops updating; the case is still progressing and the page will not say so until reloaded.

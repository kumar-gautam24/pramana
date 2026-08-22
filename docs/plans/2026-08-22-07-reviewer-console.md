# Pramana Plan 07 — Reviewer Console

> **Written retroactively, 2026-08-22**, alongside the console it describes. Tasks 1–4 are
> built. Task 5 — the `reviews.outcome` vocabulary — is **not**, and is the one thing plan 04
> explicitly handed to this plan. It is written here as an open task rather than a completed
> one.

**Goal:** The screen a clinician uses. Pick up a case the gate referred to a human, read the
evidence it assembled, watch it work, and record a determination — which is where an escalation
stops being a queue entry and becomes a decision.

**Architecture:** `apps/web`, Next.js App Router with TypeScript, served on 3000. It holds
`NEXT_PUBLIC_GATEWAY_URL` and **no other backend address**. Every screen renders on the client
and fetches with the reviewer's own token; there is no server-side data fetching, because that
would put the credential on the Next.js server and make that server a second holder of a
backend address.

**Tech stack:** Next.js 15, React 19, TypeScript. Four dependencies, all of them the framework
or its types. No UI kit, no CSS framework, no data-fetching library, no state library, no
ESLint config: five screens do not need them, and each would be a configuration surface to keep
current. `npm run lint` is `tsc --noEmit`.

## Global constraints

- **One address.** `NEXT_PUBLIC_GATEWAY_URL` is read in exactly one file. A grep for `http`
  outside `src/lib/gateway.ts` should find nothing.
- **No fallback address.** `NEXT_PUBLIC_*` is inlined at build time, so a default would be
  compiled into the image and a misconfigured build would fail by talking quietly to the wrong
  host.
- **The machine has no deny path**, so this console never renders a deny affordance for an
  automated determination ([ADR-0002](../decisions/0002-no-deny-path.md)).
- **AI use is disclosed where it is encountered** (Utah), not in a policy page.
- `node_modules` and `.next` are never committed.
- Comments explain **why**, never what. Commits: conventional, lowercase imperative, **never any
  attribution trailer.**

---

### Task 1: Shell, transport, sign-in

**Files:** `apps/web/{package.json,tsconfig.json,next.config.ts,Dockerfile,.gitignore,.env.example,README.md}`,
`src/lib/{gateway,api,types,session,format}.ts`, `src/components/{SessionProvider,AppShell,AiDisclosure}.tsx`,
`src/app/{layout,page,globals.css}`, `src/app/login/page.tsx`.

- [x] **`gateway.ts` is transport only**; `api.ts` is one function per route the console calls.
  That list is the honest answer to "what can the console do", and it is deliberately short —
  there is no case-submission call, because intake is not a reviewer's job.
- [x] **The SSE reader is written over `fetch`, not `EventSource`.** `EventSource` cannot set an
  `Authorization` header and every route behind the gateway needs a session; the alternative —
  a token in the query string — would write a live credential into every access log on the path.
  Framing follows the SSE grammar rather than assuming one `data:` line per frame.
- [x] **The token lives in `sessionStorage`**, so it dies with the tab, which is the right
  default for a shared clinical workstation. Sent as a bearer header rather than a cookie: the
  console and the gateway are separate origins over plain HTTP in development, where a
  cross-site cookie would need `SameSite=None; Secure` and would never be sent.
- [x] **`status` distinguishes "storage not read yet" from "no session"**, so a signed-in
  reviewer never sees the page flash through a login redirect on first paint.
- [x] **Sign-out clears local state first and unconditionally.** A reviewer leaving a shared
  workstation must end up signed out even if the gateway cannot be reached.
- [x] **The login failure message is the server's and no more specific.** `auth` answers 401
  identically for an unknown address and a wrong password; elaborating here would turn the form
  into an oracle for which addresses have accounts.
- [x] **The AI disclosure banner is in the app shell on every screen and on the login screen**,
  before sign-in. It is not dismissible and not collapsed.

---

### Task 2: The queue

**Files:** `src/app/cases/page.tsx`, `src/components/CaseQueueTable.tsx`, `src/hooks/useResource.ts`.

- [x] **Defaults to `outcome=escalate`** — the cases the gate referred to a person. That is the
  work.
- [x] **The approved and all-cases filters exist** so a reviewer can check what the system
  approved without being asked to. An auto-approval nobody can audit is worth less than one they
  can.
- [x] **The reason column renders the gate's sentence, not its enum value.** "The record does not
  say enough" is a different afternoon from "the record contradicts a criterion", and that is
  what tells a reviewer whether to open the case now.
- [x] **A case with no determination is shown**, with its pipeline status. One stuck in `running`
  because a worker died is precisely the case nobody would otherwise notice.
- [x] **`OutcomeBadge` enumerates the two renderable outcomes and has no default branch.** An
  unrecognised value is labelled unrecognised and shown verbatim. The guard looks redundant —
  the column is a CHECK constraint and the enum has two members — and that is the point: a
  default branch mapping an unknown value to a plausible label is the shape the failure would
  take.
- [x] **Escalate is amber, never red.** Red reads as refusal.

---

### Task 3: The case

**Files:** `src/app/cases/[caseId]/page.tsx`, `src/components/{CaseSummary,CriteriaSets,CriterionRow,EvidenceView,StepStream}.tsx`,
`src/hooks/useCaseEvents.ts`, `src/lib/determination.ts`.

- [x] **Page order is the reading order**: the request and the outcome, then the disclosure of
  what a model did and did not do, then the machine's working, then the policy criterion by
  criterion, then the form. A determination should be what a reviewer reaches after the
  evidence, not what they scroll past it to find.
- [x] **The determination is read out of the case's own audit log.** No route returns it, and the
  `decision` event carries exactly the four fields it has. Safe by construction rather than by
  luck: that event is appended only *after* the transaction holding the determination and its
  results commits — an ordering chosen for this consumer, so a console can never render an
  APPROVE for a case with no determination behind it. The last `decision` wins, because a case
  can be adjudicated again.
- [x] **Replay and the live stream are both always used**, merged on `seq`. The stored log is
  the record; the stream is subscribed to only while the case is still moving. `seq` is
  allocated inside the INSERT under a UNIQUE constraint and the Pub/Sub publish happens inside
  that same `append`, so the two views cannot render a different sequence.
- [x] **`blocking` holds two kinds of value and `CaseSummary` tells them apart** — criterion ids
  on a case that reached verification, a short-circuit marker on one that stopped earlier. A
  screen that did not distinguish them would show an empty criteria panel and no explanation,
  which is the one case where a reviewer would be right to distrust it.
- [x] **Criteria are rendered in the alternative sets the server grouped them into**
  ([ADR-0011](../decisions/0011-alternative-criteria-sets.md)). A flat list would invite the
  wrong conclusion: a criterion that fails in one set may be irrelevant in another.
- [x] **The criterion type tag is the disclosure that matters most.** `judgment` means a model
  read the notes; the other three mean a query and a comparison in code. A reviewer weighs those
  differently, so it is on the row, not in a legend.
- [x] **Confidence is shown only for judgment criteria.** A deterministic verifier records 1.0
  because arithmetic is not uncertain, and printing "100%" beside a SQL comparison would suggest
  a probability was estimated.
- [x] **Evidence renders by shape, never by subject.** No branch names a policy, a procedure or a
  clinical concept (invariant 3). It knows about quoted spans, checked records, a reason and
  scalars — each a structure, not a topic.
- [x] **Ungrounded spans are shown, marked, not dropped.** The model claimed them, they played no
  part in the verdict, and a reviewer weighing a model's reading benefits from seeing what it
  invented.
- [x] **`insufficient_evidence` is worded as a verdict, never as an error or a missing value.**
  It is what sends a case to a human.

---

### Task 4: Recording a determination

**Files:** `src/components/{ReviewForm,ReviewHistory}.tsx`.

- [x] **The form has a deny option and the machine does not.** These are the same rule twice: an
  adverse determination must come from a licensed clinician, which is exactly why the system has
  no such path and this form does. The wording beside the control says so, so a reviewer is never
  left thinking they are confirming a denial the system already made.
- [x] **`agreed_with_system` has no default and blocks submission until answered.** It is the
  field that turns clinical work into eval data, and a default would record agreement nobody
  expressed.
- [x] **The clinician is never in the payload.** `adjudication` takes it from the header the
  gateway writes after resolving the session.
- [x] **The form renders only for `clinician` and `admin`**, mirroring the gateway's `SATISFIES`
  table. The gateway is the enforcement point; this exists only so the console does not offer a
  control whose submission it knows will be refused.
- [x] **Prior reviews render above the form.** A case may be reviewed more than once, and a
  clinician about to record a decision needs to know a colleague already has. The clinician's id
  is shown, not hidden — who issued an adverse determination is the fact Illinois law is about.

---

### Task 5: Close `reviews.outcome` — **NOT DONE**

**Files:** a new `services/adjudication/migrations/000N_reviews_outcome_vocabulary.sql`,
`services/adjudication/src/adjudication/routers/cases.py`,
`apps/web/src/components/ReviewForm.tsx`.

Plan 04 left `reviews.outcome` deliberately unconstrained and named this plan as the owner,
with an explicit "must not ship open". It is still open.

What now exists that did not when that was written: the console proposes a vocabulary at the one
place a review is authored — `approve`, `deny`, `more_information` — so the column fills with
known values rather than free text. That is a constraint in one client, not in the schema, and a
second client would not inherit it.

- [ ] Settle the vocabulary as a regulatory question, not a UI one. The candidates in the
      literature are approve / deny / partial approval / pend for information; the console's
      three are a proposal, not an answer.
- [ ] Migration adding a CHECK constraint, with a data migration for anything already recorded.
- [ ] `ReviewIn.outcome` becomes a `Literal`, and its docstring's "deliberately unconstrained"
      paragraph is replaced rather than left contradicting the schema.
- [ ] The console's `OUTCOMES` constant imports the same set or is checked against it.

**Do not close this by guessing.** Putting values in the schema that no regulator recognises is
worse than an open column, because the open column is honest about not knowing.

---

## Verified

`npx tsc --noEmit` exits 0. `next build` compiles all five routes. The console has **not** been
run against a live stack, and no screenshot or click-through has been performed — the read
routes it calls were verified live during plan 05, but the rendering was not.

## Deferred out of this plan, with an owner

- **No component or end-to-end tests.** Built under an instruction to skip test-writing. The two
  things worth testing are the SSE frame parser (real parsing logic, easy to get subtly wrong)
  and `OutcomeBadge`'s refusal to label an unknown outcome — the one guard whose failure mode is
  a legal problem rather than a cosmetic one. **Owner: whoever next touches `apps/web`.**
- **Intake has no screen and no `normalize` stage.** The design numbers a first stage turning
  free text into codes; `cases` has no free-text column beyond `request_text`, which is retrieval
  input rather than a request. Plan 04 deferred the stage with the instruction that whoever
  builds intake either adds it or strikes it from the spec. This console is not that owner: it
  reads cases, it does not submit them.
- **The console never renders `determination.thresholds`** — the gate's configuration at
  decision time. The audit log's `decision` event does not carry it. If a screen ever needs it,
  that is a route, not a wider event.

---

## Self-review

**Coverage.** Implements the design's `web` service row, the SSE step audit surface, and the
Utah disclosure requirement from the regulation table. Does not cover: auth and gateway (plan
05), evals (plan 06), intake.

**The risk this work carries.** This is the only place a human meets the system, so it is the
only place the project's two hard guarantees can be quietly broken by presentation rather than
by logic. Both are defended structurally rather than by care: the outcome renderer has no
default branch, and the disclosure is a component in the shell rather than a paragraph someone
remembered to add. The undefended edge is that neither has a test, which is named above.

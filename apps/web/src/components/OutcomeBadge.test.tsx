/**
 * `OutcomeBadge`'s refusal to label an outcome it does not recognise.
 *
 * This is the one guard in the console whose failure mode is legal rather than cosmetic.
 * ADR-0002 says the machine may never issue a denial; California SB 1120 and the Medicare
 * Advantage rule reserve an adverse determination to a licensed clinician. The component's
 * own docstring argues there must be no default branch, because a default that mapped an
 * unknown value to a plausible label is exactly the shape that failure would take.
 *
 * Rendered with `react-dom/server` rather than a DOM harness. These assertions are about
 * which string reaches the reviewer, and `renderToStaticMarkup` answers that without
 * pulling in a browser environment for a component that has no behaviour.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OutcomeBadge } from "./OutcomeBadge";

const render = (outcome: string | null) =>
  renderToStaticMarkup(<OutcomeBadge outcome={outcome} />);

describe("the two outcomes the system can produce", () => {
  it("labels an approval", () => {
    const html = render("approve");
    expect(html).toContain("Approved");
    expect(html).toContain("badge--approve");
  });

  it("labels an escalation as a referral, not a refusal", () => {
    const html = render("escalate");
    expect(html).toContain("Referred to clinician");
    expect(html).toContain("badge--escalate");
  });

  it("never renders an escalation as a negative", () => {
    // Amber, never red: red reads as refusal, and an escalation is the machine handing a
    // decision to a person, which is the system working as designed.
    expect(render("escalate")).not.toContain("badge--negative");
  });

  it("says so when there is no determination yet", () => {
    const html = render(null);
    expect(html).toContain("No determination yet");
    expect(html).toContain("badge--neutral");
  });
});

describe("anything else is refused a label", () => {
  // `deny` first and by name. It is the value this console exists to never render as
  // though the system had issued it.
  const unknown = [
    "deny",
    "denied",
    "denial",
    "rejected",
    "partial_approval",
    "pend",
    "APPROVE",
    "Approve",
    " approve",
    "approve ",
    "",
    "null",
    "undefined",
  ];

  it.each(unknown)("refuses to label %o", (outcome) => {
    const html = render(outcome);
    expect(html).toContain("Unrecognised outcome");
    expect(html).toContain("badge--negative");
  });

  it("shows an unrecognised value verbatim rather than paraphrasing it", () => {
    expect(render("deny")).toContain("deny");
  });

  it("does not smuggle a recognised label into an unrecognised outcome", () => {
    const html = render("deny");
    expect(html).not.toContain("Approved");
    expect(html).not.toContain("Referred to clinician");
  });

  it("treats a differently-cased known value as unknown", () => {
    // `determinations.outcome` is lowercase by CHECK constraint. If that ever changes,
    // this must be a deliberate decision rather than a lookup that happened to miss.
    expect(render("APPROVE")).toContain("Unrecognised outcome");
  });
});

describe("keys that live on Object.prototype", () => {
  /**
   * The carried finding, now a test. `RENDERABLE` is an object literal, so
   * `RENDERABLE["constructor"]` resolves to `Object.prototype.constructor` -- truthy -- and
   * the `if (!known)` guard does not fire. The component then reads `known.className` and
   * `known.label` off a function, both `undefined`, and renders an **empty badge**: no
   * label, no warning, nothing for a reviewer to notice.
   *
   * Unreachable behind a CHECK constraint, which is the same thing that was said about
   * every other guard in this file. The whole purpose of this one is to hold when something
   * upstream has already gone wrong, and a guard that fails exactly when its premise fails
   * is not a guard.
   */
  const prototypeKeys = [
    "constructor",
    "toString",
    "valueOf",
    "hasOwnProperty",
    "__proto__",
    "isPrototypeOf",
    "propertyIsEnumerable",
    "toLocaleString",
  ];

  it.each(prototypeKeys)("refuses to label %o", (outcome) => {
    expect(render(outcome)).toContain("Unrecognised outcome");
  });

  it.each(prototypeKeys)("never renders an empty badge for %o", (outcome) => {
    // The specific corruption: `<span class="undefined"></span>`, which a reviewer reads
    // as "no outcome" when the truth is "an outcome nothing here understands".
    const html = render(outcome);
    expect(html).not.toContain('class="undefined"');
    expect(html).not.toMatch(/<span[^>]*>\s*<\/span>/);
  });
});

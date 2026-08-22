"use client";

/**
 * The eval screens, gated to the roles that can actually use them.
 *
 * The nav already hides the link, but a link is not a guard: a bookmarked or pasted URL
 * reaches the page directly, and every request it makes would then 403 one after another,
 * leaving a screen of error messages that look like the system is broken rather than like
 * the account is not an operator's.
 *
 * This is not the enforcement point — the gateway is, and it does not consult the browser.
 * The rule here mirrors `SATISFIES["operator"]` so the console never offers, or half-renders,
 * a control it knows would be refused.
 */

import { AppShell } from "@/components/AppShell";
import { useSession } from "@/components/SessionProvider";
import { mayRunEvals } from "@/lib/session";

export function OperatorOnly({ children }: { children: React.ReactNode }) {
  const { session } = useSession();

  if (session !== null && !mayRunEvals(session.user.role)) {
    return (
      <AppShell>
        <div className="stack">
          <h1>Evaluation</h1>
          <p className="notice">
            The eval harness is an operator&rsquo;s tool. A run spends model tokens and
            publishes a figure about this system&rsquo;s own accuracy; your account
            (<span className="mono">{session.user.role}</span>) may not start one.
          </p>
        </div>
      </AppShell>
    );
  }

  return <AppShell>{children}</AppShell>;
}

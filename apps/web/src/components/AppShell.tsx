"use client";

/**
 * The frame every signed-in screen renders inside: header, the standing AI disclosure,
 * and the session guard.
 *
 * The guard is here rather than in Next.js middleware because the session lives in the
 * browser (see `SessionProvider`), so the server has nothing to make the decision with.
 * It is a convenience, not a security boundary -- the gateway refuses an unauthenticated
 * request whatever this component renders.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { DisclosureBanner } from "@/components/AiDisclosure";
import { useSession } from "@/components/SessionProvider";
import { mayRunEvals } from "@/lib/session";

/**
 * Every screen this account can actually reach.
 *
 * Role-gated the same way the review form is: the gateway is the enforcement point, and this
 * list exists only so the console does not offer a link whose destination would 403. Intake
 * is open to any session -- submitting a request is the ordinary use of the system -- and the
 * eval harness is not.
 */
function navigation(role: string) {
  const links = [
    { href: "/cases", label: "Queue" },
    { href: "/cases/new", label: "Submit a case" },
  ];
  if (mayRunEvals(role as Parameters<typeof mayRunEvals>[0])) {
    links.push({ href: "/evals", label: "Evals" });
  }
  return links;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status, session, signOut } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "signed-out") router.replace("/login");
  }, [status, router]);

  // Nothing is rendered until the session is known, so a signed-in reviewer never sees
  // the page flicker through a signed-out state on first paint.
  if (status !== "signed-in" || !session) {
    return <main className="shell__main notice">Loading&hellip;</main>;
  }

  return (
    <div className="shell">
      <header className="shell__header">
        <Link href="/cases" className="shell__brand">
          Pramana<span>console</span>
        </Link>
        <nav className="shell__nav">
          {navigation(session.user.role).map((link) => (
            <Link key={link.href} href={link.href}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="shell__spacer" />
        <span className="shell__identity">
          {session.user.email} &middot; {session.user.role}
        </span>
        <button type="button" className="secondary" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <DisclosureBanner />
      <main className="shell__main">{children}</main>
    </div>
  );
}

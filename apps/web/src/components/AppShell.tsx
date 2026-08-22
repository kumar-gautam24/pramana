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
          Pramana<span>reviewer console</span>
        </Link>
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

"use client";

/**
 * The only public screen. `POST /api/auth/login` is one of exactly two routes the
 * gateway serves without a session.
 *
 * The failure message is deliberately the one the server sends and no more specific:
 * `auth` answers 401 identically for an unknown address and a wrong password, and
 * elaborating here would undo that -- it would turn this form into an oracle for which
 * addresses have accounts.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { DisclosureBanner } from "@/components/AiDisclosure";
import { useSession } from "@/components/SessionProvider";
import { GatewayError } from "@/lib/gateway";

export default function LoginPage() {
  const { status, signIn } = useSession();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (status === "signed-in") router.replace("/cases");
  }, [status, router]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password);
      router.replace("/cases");
    } catch (cause) {
      setError(
        cause instanceof GatewayError ? cause.detail : "Sign-in failed. Try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login">
      <div className="login__panel stack">
        <div>
          <h1>Pramana</h1>
          <p className="muted small">Reviewer console</p>
        </div>

        <form className="card stack" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error ? <p className="error">{error}</p> : null}
          <button type="submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {/* Disclosed before sign-in as well as after: Utah's requirement is about the
            person encountering the system, and this is the first screen they meet. */}
        <DisclosureBanner />
      </div>
    </div>
  );
}

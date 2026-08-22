-- gen_random_uuid() is built into Postgres 16 (pgcrypto folded into core).

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Lowercased by the application before it reaches here, so uniqueness is on the
    -- identity a person actually has rather than on its capitalisation.
    email text NOT NULL UNIQUE,
    -- Argon2id output, which carries its own salt and parameters in the encoded string.
    -- No plaintext password is ever stored, logged, or returned by any route.
    password_hash text NOT NULL,
    -- A CHECK, not free text. Illinois permits only a clinical peer to issue an adverse
    -- determination, so which role a user holds is a legal fact about who may do what --
    -- and a typo'd role that silently becomes a new, unhandled value is how an
    -- authorisation check ends up passing something it should have refused.
    role text NOT NULL CHECK (role IN ('clinician', 'reviewer', 'operator', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    -- The session token itself is not stored: this is a hash of it, for the same reason
    -- password_hash is a hash. A leaked database must not hand the reader live sessions.
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash text NOT NULL UNIQUE,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- Checked on every validation. A session with no expiry could never be revoked by
    -- the passage of time, which is the only revocation that needs no one to act.
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Validation looks a session up by its token hash on every proxied request, so this is
-- the index that keeps the gateway's per-request cost flat.
CREATE INDEX ix_sessions_token_hash ON sessions (token_hash);
CREATE INDEX ix_sessions_user_id ON sessions (user_id);

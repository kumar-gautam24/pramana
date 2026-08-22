# auth

**Users, passwords, sessions and roles.** Port 8004, database `pramana_auth`.

The gateway resolves every request's identity against this service and nowhere else. Nothing
downstream authenticates anyone; they trust the headers the gateway writes.

## Data model

| table | holds |
| --- | --- |
| `users` | email, argon2id password hash, role |
| `sessions` | token, user id, expiry |

Passwords are hashed with **argon2id**. The hash is never populated on the repository's read
path — the column exists, but a `User` returned from a query does not carry it, so there is no
route by which a hash reaches a response body.

## The four roles

```python
clinician | reviewer | operator | admin
```

A closed enum matching the database CHECK constraint exactly, not a free string, because
authorisation decisions branch on it.

`clinician` and `reviewer` are **not** interchangeable however similar they read: Illinois
permits only a clinical peer to issue an adverse determination, so only a `clinician` may record
a review. `admin` satisfies `operator` — an admin who could not do what an operator can would
just be a fourth role.

Where each role is enforced is in the [gateway's route table](../gateway/README.md#the-route-table).

## Endpoints

| method | path | purpose |
| --- | --- | --- |
| POST | `/login` | exchange credentials for a session token |
| POST | `/logout` | end a session |
| GET | `/session` | resolve a token to a user — what the gateway calls on every request |
| POST | `/users` | create a user |
| GET | `/users` | list users |
| POST | `/seed` | create the first admin, and only the first |
| GET | `/health` · `/ready` | liveness, readiness |

`POST /seed` is inert once **any** user exists. The guard is "the table is empty", not "the
caller supplied the right address" — a bootstrap route that can mint an admin whenever a
particular email happens to be free is not a bootstrap route.

Login returns a token and an expiry. Sessions are held in this service's database; the gateway
also uses Redis for rate-limit counters, but a session is a row here.

## Running

```bash
cd services/auth
uv run uvicorn auth.main:app --port 8004
```

Migrations run automatically at startup for this service.

## Caveats

- **`POST /users` is unauthenticated, and compose publishes port 8004.** The service's own
  docstring justifies having no admin check by saying it "is not reachable from outside the
  compose network" — and the compose file that ships contradicts that, with
  `ports: ["${AUTH_PORT:-8004}:8004"]`. So unauthenticated account creation, **including
  `role: admin`**, is available to anything that can reach the host port, and the gateway's admin
  check is bypassed by addressing 8004 directly.

  This is harmless on a laptop and is what makes the quickstart in the root README work when
  nobody has a password. It is wrong the moment this compose file is read as the shape of a
  deployment. The fix is either to drop the published port or to move the check into the service;
  the argument for not duplicating the check depends on a premise the compose file falsifies.
- **No test suite.** This service is carried substantially from
  [Deflect](https://github.com/kumar-gautam24/deflect), where it was reviewed and ran under
  production-shaped conditions. It has no tests of its own here.
- **Passwords are not recoverable and are not recorded anywhere.** If the accounts on a running
  stack have unknown passwords, create a new one rather than trying to recover the old.
- **`scripts/migrate.py` cannot be run from this directory** — it imports one of `policy`,
  `member` or `adjudication`. Not blocking, because this service migrates itself.

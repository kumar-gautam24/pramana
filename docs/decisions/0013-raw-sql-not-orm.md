# ADR-0013 — Raw SQL and hand-written migrations, not an ORM

**Status:** accepted, 2026-08-16. Supersedes the implicit choice made in plan 02 and carried
into plan 03.

## Context

`policy` and `member` were built on SQLAlchemy's declarative ORM with Alembic migrations,
because that is the default a Python service reaches for. Two plans in, the evidence from this
project itself is mixed at best.

**Alembic's autogenerate has produced a defect in each of the two services it has touched.** In
`policy` it emitted a migration referencing `pgvector.sqlalchemy` without importing it, which
would have raised `NameError` on first run, and it failed to emit `CREATE EXTENSION vector` at
all. In `member` the generated column needed checking by hand. Both were caught by review, not
by the tool. A migration generator that must be read line by line before it is trusted is not
saving the work it claims to save.

**The ORM's unit of work introduced a hazard that raw SQL does not have.** `member`'s models
declare foreign keys without `relationship()`, so SQLAlchemy has no dependency graph to sort a
flush by, and adding a parent and child in one `flush()` raises a foreign-key violation. That
cost debugging time in two separate tasks, and the fix — "flush parents first" — is a rule
about the ORM's internals, not about the domain.

Against that, the ORM was buying: declarative schema as the migration source, typed attribute
access, and session-scoped transactional test fixtures. All three are replaceable.

## Decision

Use **raw SQL** for queries and **hand-written, numbered SQL files** for migrations. No
declarative ORM, no Alembic.

- **Queries**: SQL written out in full, executed through `asyncpg`, with results mapped
  explicitly onto frozen dataclasses. A query a reviewer can read is a query a reviewer can
  check against the policy it implements.
- **Migrations**: `migrations/0001_*.sql`, `0002_*.sql`, applied in order by a small runner that
  records applied versions in a `schema_migrations` table and wraps each file in a transaction.
  Roughly fifty lines, and every statement is one a person wrote deliberately.
- **Models**: frozen dataclasses. Row-to-object mapping is explicit rather than inferred.

## Consequences

The flush-ordering hazard disappears — there is no unit of work to order. So does the class of
surprise where an attribute access issues a query.

Every statement reaching the database becomes reviewable as SQL. In a system a state insurance
commissioner may audit, "show me the query that decided this" having a literal answer is worth
more than it would be in most applications.

The cost is real and must not be pretended away: **the runner has to be transactional and has
to record what it applied**, or environments drift silently, which is worse than anything
Alembic does. That is the one piece of this decision that is load-bearing. Column changes now
need SQL written by hand, and there is no schema diff to catch a model that has drifted from
its table — the schema *is* the SQL, so drift means a query fails, which is at least loud.

`pgvector` needs its asyncpg type codec registered on each connection, where the SQLAlchemy
dialect handled it. That is one function call at pool setup.

Retrofitting `policy` and `member` is scheduled as a sweep rather than done opportunistically,
so the two services do not sit in different styles while later plans build on both. See
`docs/plans/2026-08-16-S1-sql-sweep.md`.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md) — deterministic criteria are
checked in SQL, and this makes the SQL that does it directly legible.

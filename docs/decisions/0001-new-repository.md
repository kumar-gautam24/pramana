# ADR-0001 — New repository rather than pivoting Deflect

**Status:** accepted, 2026-08-15

## Context

Deflect is a working RAG system with a confidence gate over FastAPI's documentation: 166
commits, 460 tests, green CI, public since 2026-08-05. Its infrastructure — gateway, service
skeleton, job queue, eval harness — is directly reusable here. The domain, however, changes
completely: corpus, schemas, service responsibilities, UI and eval metrics all differ.

## Decision

Start a new repository. Carry the infrastructure over deliberately, and state the lineage in
the README rather than obscuring it.

## Consequences

Deflect stays standing as a finished, coherent project, including its measured finding that
reranking degrades ranking while remaining necessary for the gate. Pramana's history starts
clean and every commit is about prior authorization.

The alternative — renaming five services and rewriting every schema inside the existing
history — produces a repository whose first 166 commits are visibly about something else.

Naming the reuse converts it into evidence of judgment. Two coherent repositories, where the
second obviously stands on the first, read better than one that changed its mind.

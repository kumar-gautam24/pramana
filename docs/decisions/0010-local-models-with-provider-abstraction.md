# ADR-0010 — Local models for development, provider abstraction retained

**Status:** accepted, 2026-08-15

## Context

Per-criterion verification means roughly five to ten model calls per case, against the
predecessor's one call per question. On a free hosted tier that turns an eval run into hours.
Development hardware is an M1 Pro with 32 GB of unified memory.

## Decision

Run Qwen2.5-14B-Instruct locally through Ollama (≈9 GB at Q4), alongside the existing
bge-small embedder and ms-marco-MiniLM reranker. Keep the provider abstraction so a hosted
model can be selected by configuration for headline eval runs.

Services refuse to start if the configured model cannot produce schema-constrained output.

## Consequences

Development is free, unmetered and reproducible, with no API key required in CI.

Local models adhere to strict schemas less reliably than hosted ones, and this system depends
on citation-constrained JSON. The startup guard converts that risk into a boot failure rather
than a silent degradation on the first request.

Keeping the abstraction means the model is a configuration choice, so the eventual comparison
across 14B, 32B and a hosted model is a config sweep rather than a rewrite — and becomes
another measured table alongside the retrieval ablation.

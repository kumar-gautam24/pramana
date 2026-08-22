# ADR-0014 — One schema, three providers, no translation

**Status:** accepted, 2026-08-19

## Context

[ADR-0010](0010-local-models-with-provider-abstraction.md) said the model is a configuration
choice and kept a provider abstraction to make it one. It did not say what that abstraction
has to hold constant, and building the second and third providers made the question concrete.

Criteria extraction is the one place in this system where a model's output drives control
flow. It is constrained by a JSON Schema that `services/extract.py` builds per call, and that
schema has three properties the providers disagree about:

- `$defs` and `$ref`, because it is generated from nested pydantic models;
- a `params` object that is deliberately **open** — its shape depends on the criterion type,
  and `domain/params.py::validate_params` is what closes it;
- a `source_chunk_id` enumerating the ids actually retrieved for this case, so the model
  cannot cite a passage it was never shown.

Each provider offers a different way to carry a schema, and the tempting ones are lossy:

| provider | the obvious field | what it costs |
| --- | --- | --- |
| Ollama | `format` | takes the schema as written |
| Gemini | `responseSchema` | a cut-down OpenAPI dialect: rejects `$defs`/`$ref`, cannot express a free-form object |
| Gemini | `responseJsonSchema` | takes the schema as written |
| OpenAI-compatible (Groq, OpenRouter, Together, vLLM) | `response_format` with `strict: true` | requires every object to close `additionalProperties` and list every property in `required` — which `params` cannot do |
| OpenAI-compatible | `response_format` without `strict` | takes the schema as written |

## Decision

**A provider carries the caller's schema unmodified or it is not a provider.** Gemini gets it
in `responseJsonSchema`, never `responseSchema`. The OpenAI-compatible client sets
`response_format: {"type": "json_schema"}` and deliberately does **not** set `strict`.

Every provider raises `UpstreamUnavailable` and never a provider-specific exception, so
callers catch one type regardless of which model is configured. `build_provider` is the one
place a provider name becomes a provider; nothing above `services/llm.py` can tell which one
it holds.

Post-validation is not relaxed anywhere because a provider constrains the answer.
`validate_params` runs on every extracted criterion whichever provider produced it —
constrain the answer, then check it anyway.

Context caching is not used, on any provider.

## Consequences

The cost of this rule is real: dropping `strict` gives up the strongest guarantee one provider
offers, and `responseJsonSchema` is the newer and less-travelled of Gemini's two fields.

What it buys is that a comparison across providers measures the models rather than the
harness. A provider that quietly simplified the schema would constrain the model differently
from its siblings while every test still passed — and the eval numbers that comparison
produces are the point of the project. That drift is what a provider abstraction exists to
prevent; it is not a detail of one.

Adding a provider of the OpenAI dialect is a base URL and an enum member, not a class. Adding
a genuinely different one is a class implementing `chat` and one arm in `build_provider`, and
the class's docstring must say what it did to pass the schema through intact.

Not caching context is a real token cost on a large system prompt. It is paid deliberately: a
cache is a second thing that can be stale while looking fresh, and an audit that cannot say
which bytes the model actually saw is worth less than the tokens it saves.

**Verified, not assumed.** Groq was run against the real extraction schema: `$defs`, `$ref`,
the open `params` object and the `source_chunk_id` enum all survive intact, and the model
returned NCD 240.4's rule in the disjunctive normal form [ADR-0011](0011-alternative-criteria-sets.md)
expects. Gemini's `responseJsonSchema` path is written but has not been exercised against the
live API — the key available at the time was rejected by Google — and this ADR should not be
read as claiming otherwise.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md),
[ADR-0010](0010-local-models-with-provider-abstraction.md),
[ADR-0016](0016-closed-vocabularies-for-facts.md)

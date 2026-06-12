# CLAUDE.md — hermes-agent Constitution

## Identity
This repository is the **Python/FastAPI backend**. It provides endpoints and
data processing for the workspace.

## Scope Guard
- You provide **endpoints and data processing only**.
- Do **NOT** write UI code. UI lives in `open-webui`.
- Do **NOT** implement authentication/authorization internals. That belongs to
  `security`.

## Risk Levels
| Level | Policy | Applies To |
|-------|--------|------------|
| **R0 — STOP & ASK** | Halt and request explicit human approval. | Destructive data operations, deleting endpoints in active use. |
| **R1 — Notify** | Proceed, but flag the change clearly in your summary. | **Database migrations**, **schema changes**, or **API response shape alterations**. |
| **R2 — Execute** | Proceed autonomously. | New endpoints within spec, internal refactors, query optimizations, tests. |

## Infrastructure Context
- Target runtime: **PostgreSQL 18** and **Redis 8**.
- Write all database and caching logic optimized specifically for these
  versions. You may use version-specific features; do not write
  lowest-common-denominator SQL for older Postgres versions.

## Data Contract
- API shapes **MUST** strictly adhere to `../shared-contracts/api-responses.ts`.
- If the spec does not match your logic, **halt and ask for a spec update**.
  Never ship a response shape that diverges from the contract.

## Handoff Protocol
When completing an endpoint, generate a `handoff.md` file in this repository
detailing:
1. **Route** — method + path (e.g. `POST /api/v1/feature-x`).
2. **Confirmed data shape** — the actual request/response JSON as implemented,
   cross-referenced against `../shared-contracts/api-responses.ts`.
3. **Missing edge cases** — any error paths, validation gaps, or unhandled
   states the frontend or security layer must know about.

The frontend (`open-webui`) reads `handoff.md` before integrating; treat it as
a deliverable, not documentation overhead.

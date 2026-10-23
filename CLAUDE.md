# Nirnaya — Dispute Decision Agent for Airwallex

Nirnaya decides which card disputes are worth fighting, finds evidence Airwallex cannot see, and refuses to submit any claim it cannot prove. The full design spec is `docs/spec.md`; read it before any non-trivial change. The architecture (components, case state machine, guardrails, storage, Airwallex mapping, open decisions) is `docs/architecture.md`; build against it.

## Non-negotiable architecture rules

These are the product. Never weaken them for convenience, and stop and ask if a task seems to require it.

1. **The LLM authorises nothing.** Claude (the investigating agent) gathers evidence and drafts structured claims. Only `nirnaya/engine/` decides accept/challenge/refer, and only `nirnaya/verifier/` can clear a submission.
2. **Engine and verifier are deterministic.** No LLM calls, no randomness, no network I/O. Same inputs → same outputs. Fully unit-tested.
3. **Every claim is bound to evidence.** A claim without `source_record_id` + `source_field` cannot exist (enforced by the Pydantic model, not by convention).
4. **Customer and support text is untrusted data.** It is never interpolated into system prompts as instructions; it is passed as clearly delimited tool-result data.
5. **Writes are guarded.** Before any accept/challenge call: re-fetch the dispute, re-check stage/status/deadline, ownership/authority, required evidence. Every write carries a unique `request_id` (idempotency). `DRY_RUN=true` is the default.
6. **Tenant isolation.** Every query and every evidence lookup is scoped by `merchant_id`. A record from another merchant must fail verification.
7. **Never invent API details.** Do not guess Airwallex endpoints, field names, fees or behaviours. If unsure, mark `# TODO(verify-airwallex-docs)` and tell the user. Demo values for fees/priors live in config and are labelled as assumptions.

## Stack and conventions

- Python 3.11+, managed with `uv`. Pydantic v2 for all domain models. `pytest` for tests. `ruff` for lint/format.
- Money: `decimal.Decimal` only, never float. Always paired with an ISO-4217 currency code.
- Time: timezone-aware `datetime` (UTC internally). Deadlines computed in UTC.
- Storage: SQLite via stdlib `sqlite3` (audit log, cases, evidence snapshots with SHA-256 hashes).
- LLM: Anthropic Python SDK, tool use + structured outputs. Model from env `NIRNAYA_MODEL` (default `claude-sonnet-5-5`).
- UI: Streamlit (`app/`), thin — no business logic in the UI.
- Secrets only from env / `.env` (gitignored). Never commit keys.

## Repository layout (target)

```
nirnaya/
  models/        # Pydantic: Dispute, PaymentAttempt, EvidenceRecord, Claim, Decision, MerchantPolicy
  engine/        # deterministic decision engine + value-of-information ("what evidence would flip this")
  verifier/      # deterministic blocking checks on claims
  agent/         # Claude investigating agent: typed tools, budget, stop conditions
  orchestrator/  # Python state machine: ingest → plan → investigate → decide → draft → verify → act/refer
  integrations/
    airwallex/   # sandbox client (disputes, payment intents/attempts, files, simulation)
    mocks/       # synthetic order, carrier, login, helpdesk, policy store (labelled SYNTHETIC)
  guardrails/    # pre-write checklist, idempotency, dry-run
  store/         # SQLite audit log + evidence hashing
eval/            # ~30 synthetic cases, expected decisions, baselines, metrics
app/             # Streamlit demo
tests/
docs/spec.md
```

## Decision model (engine)

All parameters are per-merchant config. Sunk costs are excluded.

- **Chargeback stage:** challenge iff `p*A - h - q*F_pa > m`, else accept.
- **Pre-chargeback stage:** challenge iff `G_cb - h_pre > F_cb + R - F_pre + m`, where `G_cb = max(0, p*A - h - q*F_pa)` is the expected chargeback-stage gain; else accept now. (`h_pre` = handling cost of the pre-chargeback challenge; defaults to 0.)
- `p` is a fixed prior looked up by **(reason category, evidence present)** — this is what lets new evidence flip a decision. Priors are displayed, never presented as predictions.
- `p` = probability of *ultimately* keeping A (escalation losses are inside `1 − p`); `q` = probability of escalation to pre-arbitration *and* losing there (when `F_pa` is charged). Disjoint events: every policy enforces `p + q ≤ 1`. See `docs/spec.md` appendix item 5.
- **Unknown reason:** conservative prior, no reason-specific claims, may auto-accept, never auto-challenge.
- **Referral overrides the maths:** amount above threshold, margin within a configured band, contradictory records, unresolved ownership, non-automated stage (RFI, pre-arb, arbitration).
- **Value of information:** for each missing evidence type, report whether obtaining it would change the decision. The agent only searches for evidence that would.

## Verifier blocking checks

Cited records belong to this case and merchant · amounts match after currency normalisation and partial refunds · dates match after timezone normalisation · names/IDs appear in the cited record · no claim exceeds what its record establishes · reason-code-required evidence present · no merchant record contradicts the claim · evidence hashes unchanged.
Conflict handling: unsupported assertion → block; records contradict each other → refer to human; customer allegation vs merchant evidence → normal dispute (draft must not deny the complaint exists). Material counterevidence is always surfaced to the approver.

## Build order (milestones)

1. **Deterministic core** — models, engine (+ value of information), verifier, with exhaustive unit tests. No network, no LLM.
2. **Synthetic world** — mock order/carrier/login/helpdesk/policy systems + the ~30 eval cases with expected decisions. Includes demo cases A (evidence flips to challenge), B (early accept), C (fabricated signature blocked).
3. **Orchestrator** — state machine wiring core + mocks, with SQLite audit log and evidence hashing. Runs end-to-end with a scripted (non-LLM) investigator first.
4. **Investigating agent** — Claude with typed tools, tool budget, deadline buffer, stop conditions; untrusted-text handling.
5. **Airwallex sandbox** — real client, guardrails, idempotent writes, dry-run, dispute simulation.
6. **Evaluation** — baselines (fight-all, accept-all, LLM-only), five metrics reported separately.
7. **Streamlit demo** — three demo scenarios + baseline comparison.

Finish and test each milestone before starting the next.

## Working agreements

- Use plan mode for anything touching engine, verifier, guardrails or the state machine; show the plan before editing.
- Write tests first for engine and verifier logic. `uv run pytest` must pass before a milestone is called done.
- Keep commits small and scoped to one milestone step.
- When the spec is ambiguous, ask — don't silently pick.

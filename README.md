# Nirnaya

**A dispute decision agent for Airwallex merchants.** Nirnaya decides which card disputes are worth fighting, finds the evidence Airwallex cannot see, and refuses to submit any claim it cannot prove.

Built for the Airwallex Developer Lab hackathon, starter kit 4: Dispute Response Agent.

## How it works

Authority is split three ways, so the model never decides anything on its own:

| Part | Role | Code |
|---|---|---|
| **Investigating agent** (LLM) | Searches the merchant's systems for evidence and drafts structured claims. It authorises nothing. | `nirnaya/agent/`, `nirnaya/llm/` |
| **Decision engine** (deterministic) | Accepts, challenges or refers, using the merchant's stage-specific economics. It also names the missing evidence that would change its mind, and the agent searches only for that. | `nirnaya/engine/` |
| **Verifier** (deterministic) | Checks every claim against its source record: amounts, dates, names, what the record actually proves, consistency across records, tenant and hash. Any failure blocks submission. | `nirnaya/verifier/` |

A Python state machine (`nirnaya/orchestrator/`) runs each case. Every write to Airwallex goes through one guarded gateway (`nirnaya/guardrails/`), which:
- re-fetches the dispute;
- re-checks stage, status, amount, deadline and authority;
- re-runs the decision;
- sends an idempotent `request_id`.

Hard cases go to a person (review queue), with the reason and the evidence. **`DRY_RUN=true` is the default.**

The full design is in [`docs/spec.md`](docs/spec.md) and [`docs/architecture.md`](docs/architecture.md).

## Setup

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # then fill in what you need (see below)
uv run pytest           # about 1,300 offline tests; no network, no keys needed
```

`.env` keys. Everything is optional for the offline demo:

| Key | For |
|---|---|
| `NIRNAYA_AGENT` | `scripted` (default, no model) or `llm` |
| `NIRNAYA_LLM_PROVIDER`, `NIRNAYA_MODEL` | `anthropic` (default, `claude-sonnet-5-5`) or `openai` (model required) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | When `NIRNAYA_AGENT=llm` |
| `AIRWALLEX_CLIENT_ID`, `AIRWALLEX_API_KEY`, `AIRWALLEX_BASE_URL` | The live sandbox runner (`https://api.sandbox.airwallex.com`) |
| `DRY_RUN` | Keep `true`; writes are sent only with `false` and the runner's `--submit` |

## Run it

| What | Command |
|---|---|
| **Demo console** (kit 4 cases, review queue, evaluation) | `uv run streamlit run app/streamlit_app.py`, then open http://localhost:8501 |
| Kit 4 in the terminal (offline) | `uv run python -m nirnaya.demo` |
| **Evaluation report** (Nirnaya against three baselines) | `uv run python -m nirnaya.evaluation.report` |
| LLM-only baseline (opt-in, about 40 model calls, cached) | `NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.llm_baseline` |
| Kit 4 on the **Airwallex sandbox** | `uv run python -m nirnaya.sandbox_demo` (add `--checkout` to pay on Airwallex's hosted page, `--offline` for the fake, `--submit` for real writes) |
| Read-only sandbox checks | `NIRNAYA_LIVE_AIRWALLEX=1 uv run pytest -m live_airwallex -s` |

## The kit 4 cases

| Case | What happens |
|---|---|
| **1. Large fraud claim** (1,500 USD, RFI, Visa 10.4) | On Airwallex's data alone, Nirnaya refers, because an RFI accept would refund. It finds three prior undisputed orders from the same device and IP and challenges. |
| **1b. The issuer rejects the evidence** | The case escalates to chargeback, and Nirnaya re-decides and challenges again. |
| **2. Small not-received claim** (15 USD, chargeback, Visa 13.1) | Fighting costs more than the amount, so Nirnaya accepts (`LOW_VALUE_TRANSACTION`). |
| **2-C. A fabricated signature** | An unguarded draft says "signed for by J. Smith". The carrier record has no signature, so the verifier blocks it and says why. |
| **3. Credit not processed** (120 USD, chargeback, Visa 13.6) | The customer emailed support twice with no reply. The maths would accept, but a person decides. |

## Evaluation (42 synthetic cases)

| | Nirnaya | Fight everything | Accept everything | LLM only, naive (Claude) | LLM only, informed (Claude) | LLM only, naive (gpt-5-mini) |
|---|---|---|---|---|---|---|
| Expected net loss (USD) | **11,417.40** | 11,800.60 | 17,652.00 | 11,495.60 | 11,882.40 | 11,687.60 |
| Agrees with the expert decision | **42/42** | 18/42 | 14/42 | 28/42 | 34/42 | 22/42 |
| Unsupported claims submitted | **0** | 6 | 0 | 18 | 10 | 84 |
| Merchant-policy violations | **0** | 13 | 11 | 8 | 1 | 12 |

The LLM-only baseline gives a model the dispute and every record and lets it decide and draft alone, with no engine or verifier. *Naive* adds only the fees. *Informed* also gives it the merchant's rules from the policy file: when to escalate, the costs, the win priors, FX, the stage formulas, whose records may be cited, and which record field establishes which fact. Both Claude runs use `claude-sonnet-5-5`; answers are cached in `eval/results/llm_only/` with the prompt versions used. Told the rules, the model follows policy far better, but it still submits claims its records do not support. Known gaps in informed prompt v1: it does not map Airwallex reason types to the policy's reason categories, so five subscription and fraud cases were treated as unknown reasons. And no prompt can see an unresolved payment ownership, which is not in any record.

These are expected values under the expert policy's assumed priors and fees ([`eval/EXPERT_POLICY.md`](eval/EXPERT_POLICY.md)) on **synthetic** cases, not real issuer outcomes. The full report is `eval/results/report.md`.

## Versioned prompts and configuration

Prompts, the demo policy and priors, the FX snapshot and the evaluation economics are files in `nirnaya/assets/`, one per version, pinned in `nirnaya/assets/active.toml` and SHA-256 locked. A published version never changes: add `v2`, then run `uv run python -m nirnaya.assets lock`. To try a prompt version without changing the pin, set `NIRNAYA_PROMPTS=drafter_system:v2`. Agent runs and reports record the versions they used (see architecture §16.1).

## Honest limits

- **Merchant systems are synthetic.** The order, carrier, login, helpdesk and policy data are mocks labelled SYNTHETIC. Airwallex is real in the sandbox runner and an in-memory fake elsewhere.
- **Win probabilities are fixed priors** shown as priors, not predictions. Fees and priors are merchant configuration; the demo values are assumptions.
- **The live sandbox run** depends on sandbox card processing. Airwallex reported degraded sandbox simulation features from 26 October 2026.
- **Unresolved Airwallex details** are marked `TODO(verify-airwallex-docs)` in the code rather than guessed.

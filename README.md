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

## Device and IP on a chargeback challenge

At the chargeback stage Nirnaya also sends Airwallex the customer's device ID and IP address (`customer_info`), taken only from the merchant's own order record that the verified claims cite, and only if valid. In the kit, K1's second challenge (after the issuer rejects the first) carries them.

## Airwallex AI beside Nirnaya

Airwallex's AI Dispute Automation can recommend Challenge or Accept for a dispute at the chargeback stage. Nirnaya reads that recommendation and shows it next to its own decision, with its reasons when they differ, for example: *"Airwallex AI recommends Challenge; Nirnaya decides REFER to a person (UNANSWERED_CUSTOMER_CONTACT)."* It is a comparison, never an input: the engine does not read it. Offline, the kit cases carry clearly labelled **simulated** recommendations to show the display; on the live sandbox the real one is used.

## Evaluation (42 synthetic cases)

| | Nirnaya | Fight everything | Accept everything | LLM only: naive v1 | LLM only: informed v1 | LLM only: informed v2 |
|---|---|---|---|---|---|---|
| Expected net loss (USD) | 11,417.40 | 11,800.60 | 17,652.00 | 11,495.60 | 11,882.40 | 11,342.40 |
| Agrees with the expert decision | **42/42** | 18/42 | 14/42 | 28/42 | 34/42 | 39/42 |
| Unsupported claims submitted | **0** | 6 | 0 | 18 | 10 | 4 |
| Merchant-policy violations | **0** | 13 | 11 | 8 | 1 | 1 |

The LLM-only baseline gives a model (`claude-sonnet-5-5`) the dispute and every record and lets it decide and draft alone, with no engine or verifier. *Naive* adds only the fees. *Informed* also gives it the merchant's rules from the policy file: when to escalate, the costs, the win priors, FX, the stage formulas, whose records may be cited, and which record field establishes which fact. Informed v2 adds the mapping from Airwallex reason types to the policy's reasons and the exact marginal-band rule, both missing from v1.

Prompting closes most of the decision gap, and informed v2 even shows 75 USD less expected loss than Nirnaya. That 75 USD is three human reviews (25 USD each) it skipped on cases a person should see: A3, where it submitted two claims its records do not support; A5, where payment ownership is unresolved (a policy violation; the fact is not in any record); and S2, where it challenged without the evidence the reason code requires. The expected-loss metric charges for a review but puts no price on submitting an unsupported claim. Nirnaya's 0 unsupported claims and 0 violations hold by construction, on every run; the model's numbers are one run of a non-deterministic model.

**Same model, same prompts, three runs (informed v2):**

| Run | Agrees with the expert | Unsupported claims | Policy violations |
|---|---|---|---|
| 1 | 39/42 | 4 | 1 |
| 2 | 39/42 | 4 | 1 |
| 3 | 38/42 | 10 | 2 |

One decision changed between runs (F8), and six cases changed how many unsupported claims the model submitted. K1, the kit's headline fraud case, had none in runs 1 and 2 and three in run 3. Nirnaya gives the same result on every run, with 0 unsupported claims by construction.

Answers are cached in `eval/results/llm_only/<variant>-<version>.json` (repeat runs as `.runN.json`) with the prompt versions and case set used. An earlier naive v1 run on gpt-5-mini (22/42, 84 unsupported claims, 12 violations) is kept in `eval/results/llm_only/archive/`.

**On the 42/42:** the expected routes were derived by hand from the written expert policy, by the same author as the engine, so this measures self-consistency (the code follows its policy), not that the policy is right. The independent evidence is the comparison with the LLM-only baseline and the unsupported-claim counts.

These are expected values under the expert policy's assumed priors and fees ([`eval/EXPERT_POLICY.md`](eval/EXPERT_POLICY.md)) on **synthetic** cases, not real issuer outcomes. The full report is `eval/results/report.md`.

## Versioned prompts and configuration

Prompts, the demo policy and priors, the FX snapshot and the evaluation economics are files in `nirnaya/assets/`, one per version, pinned in `nirnaya/assets/active.toml` and SHA-256 locked. A published version never changes: add `v2`, then run `uv run python -m nirnaya.assets lock`. To try a prompt version without changing the pin, set `NIRNAYA_PROMPTS=drafter_system:v2`. Agent runs and reports record the versions they used (see architecture §16.1).

## LangSmith (optional)

Set `LANGSMITH_API_KEY` (and `LANGSMITH_ENDPOINT` for a non-default region) in `.env`.

- **Trace every case:** `LANGSMITH_TRACING=true` (project `LANGSMITH_PROJECT`, default `nirnaya`). Each case is one tree: its state-machine steps in order (ingested, investigating, decided, drafting, verifying, ... submitted or referred), each with what was decided there, and the model calls inside the step that made them, with the prompt, reply, tool calls, tokens and prompt versions.
- **Mirror the prompt versions:** `uv run python -m nirnaya.integrations.langsmith prompts` (private, read-only; the repo is the source of truth).
- **Upload the evaluation as experiments:** `uv run python -m nirnaya.evaluation.report`, then `uv run python -m nirnaya.integrations.langsmith experiments`.

## Honest limits

- **Merchant systems are synthetic.** The order, carrier, login, helpdesk and policy data are mocks labelled SYNTHETIC. Airwallex is real in the sandbox runner and an in-memory fake elsewhere.
- **Win probabilities are fixed priors** shown as priors, not predictions. Fees and priors are merchant configuration; the demo values are assumptions.
- **The live sandbox run** depends on sandbox card processing. Airwallex reported degraded sandbox simulation features from 26 October 2026.
- **Unresolved Airwallex details** are marked `TODO(verify-airwallex-docs)` in the code rather than guessed.
- **Evidence completeness is only as good as the contradiction catalogue.** Before any challenge is drafted, code fetches every record that could contradict it (so the model cannot hide one by not looking), but "could contradict" means a rule in `nirnaya/evidence/catalogue.py`; a kind of counterevidence with no rule is not sought.

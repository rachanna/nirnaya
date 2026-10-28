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
uv run pytest           # about 1,470 offline tests; no network, no keys needed
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
| Agentic suite: reading notes (offline; cached model runs) | `uv run python -m nirnaya.evaluation.agentic` (live: `NIRNAYA_LIVE_LLM=1 ... --live`, 10 model calls) |
| Kit 4 on the **Airwallex sandbox** | `uv run python -m nirnaya.sandbox_demo` (add `--checkout` to pay on Airwallex's hosted page, `--offline` for the fake, `--submit` for real writes) |
| Read-only sandbox checks | `NIRNAYA_LIVE_AIRWALLEX=1 uv run pytest -m live_airwallex -s` |

## The kit 4 cases

| Case | What happens |
|---|---|
| **1. Large fraud claim** (1,500 USD, RFI, Visa 10.4) | On Airwallex's data alone, Nirnaya refers, because an RFI accept would refund. Its intake looks up the merchant's order record (by code), which shows three earlier undisputed orders from the same device and IP, so it challenges. The investigating agent is not needed here; N1 and S5 show it finding the evidence that flips a decision. |
| **1b. The issuer rejects the evidence** | The case escalates to chargeback, and Nirnaya re-decides and challenges again. |
| **2. Small not-received claim** (15 USD, chargeback, Visa 13.1) | Fighting costs more than the amount, so Nirnaya accepts (`LOW_VALUE_TRANSACTION`). |
| **2-C. A fabricated signature** | An unguarded draft says "signed for by J. Smith". The carrier record has no signature, so the verifier blocks it and says why. |
| **3. Credit not processed** (120 USD, chargeback, Visa 13.6) | The customer emailed support twice with no reply. The maths would accept, but a person decides. |

## Device and IP on a chargeback challenge

At the chargeback stage Nirnaya also sends Airwallex the customer's device ID and IP address (`customer_info`), taken only from the merchant's own order record that the verified claims cite, and only if valid. In the kit, K1's second challenge (after the issuer rejects the first) carries them.

## Airwallex AI beside Nirnaya

Airwallex's AI Dispute Automation can recommend Challenge or Accept for a dispute at the chargeback stage. Nirnaya reads that recommendation and shows it next to its own decision, with its reasons when they differ, for example: *"Airwallex AI recommends Challenge; Nirnaya decides REFER to a person (UNANSWERED_CUSTOMER_CONTACT)."* It is a comparison, never an input: the engine does not read it. Offline, the kit cases carry clearly labelled **simulated** recommendations to show the display; on the live sandbox Nirnaya reads the real one if Airwallex sends it (not yet observed: sandbox card payments are failing).

## Evaluation (42 synthetic cases)

| | Nirnaya | Fight everything | Accept everything | Claude only: naive | Claude only: informed v2 | Claude only: informed v3 |
|---|---|---|---|---|---|---|
| Expected net loss (USD) | 11,417.40 | 11,800.60 | 17,652.00 | 11,495.60 | 11,342.40 | 11,342.40 |
| Agrees with the expert decision | **42/42** | 18/42 | 14/42 | 28/42 | 39/42 | 39/42 |
| Claims a record does not support | **0** | 6 | 0 | 5 | 0 | 0 |
| True facts written in the wrong form (fail exact-value checks) | **0** | 0 | 0 | 13 | 4 | 0 |
| Merchant-policy violations | **0** | 13 | 11 | 8 | 1 | 1 |

The Claude-only baseline gives `claude-sonnet-5-5` the dispute and every record and lets it decide and draft alone, with no engine or verifier. *Naive* adds only the fees. *Informed* also gives it the merchant's rules from the policy file: when to escalate, the costs, the win priors, FX, the stage formulas, whose records may be cited, and which record field establishes which fact (v2 adds the reason-type mapping and the exact marginal-band rule; v3 adds the same exact-value guidance Nirnaya's own drafter gets). An informed v1 run (34/42) is in the full report.

**What this shows, honestly.** On these synthetic cases, a well-informed model nearly matches Nirnaya. Informed v3 agrees on 39 of 42 and submits no claim its records fail to support. Its three differences all come from something it was not given or from the fixtures:
- **S2:** it challenged a subscription dispute with only the accepted terms. The rule that a subscription defence needs a second piece of evidence was not in its prompt, so it was silently not applied. In Nirnaya that rule is code, tested, and enforced on every case.
- **A5:** payment ownership is unresolved, which is not in any record it saw (the one policy violation).
- **A3:** expected "refer" only because the scripted side drafts a deliberately careless claim there; the model's own claims were correct.

Its 75 USD lower expected loss is exactly those three skipped human reviews. Naive prompting did overreach (another merchant's record, a reason-specific claim on an unknown reason), and earlier informed runs wrote true facts as sentences where an exact value was needed.

So Nirnaya's advantage here is not better judgement than a well-prompted model. It is that its guarantees do not depend on the prompt or the run:
- every rule is code, so none can be left out of a prompt;
- every submitted claim is bound to a record field and checked against it;
- referrals are enforced;
- the result is identical every run;
- the model never controls a write.

**Same model, same prompts, three runs (informed v2):**

| Run | Agrees with the expert | Claims failing exact-value checks (all true facts in the wrong form) | Policy violations |
|---|---|---|---|
| 1 | 39/42 | 4 | 1 |
| 2 | 39/42 | 4 | 1 |
| 3 | 38/42 | 10 | 2 |

One decision changed between runs (F8), and six cases changed how many claims failed (K1: 0, 0, then 3). Nirnaya gives the same result on every run.

**How sensitive are the decisions?** The fees and q are assumptions, so the report re-evaluates every maths action with one changed at a time: raising q from 0.10 to 0.25 flips 14 of 40, a pre-arbitration fee of 1,000 instead of 500 flips 12, and shifting every prior by 0.10 flips 3 to 4. Five cases sit within 0.05 of their break-even prior. A merchant must set these numbers; `docs/calibration.md` specifies how to learn the priors from outcomes.

Answers are cached in `eval/results/llm_only/<variant>-<version>.json` (repeat runs as `.runN.json`) with the prompt versions and case set used. An earlier naive v1 run on gpt-5-mini (22/42, 84 unsupported claims, 12 violations) is kept in `eval/results/llm_only/archive/`.

**On the 42/42:** the expected routes were derived by hand from the written expert policy, by the same author as the engine, so this measures self-consistency (the code follows its policy), not that the policy is right. The independent evidence is the comparison with the LLM-only baseline and the unsupported-claim counts.

These are expected values under the expert policy's assumed priors and fees ([`eval/EXPERT_POLICY.md`](eval/EXPERT_POLICY.md)) on **synthetic** cases, not real issuer outcomes. The full report is `eval/results/report.md`.

## The agent reads notes (agentic suite, 10 synthetic cases)

On the 42 cases every fact sits in a structured field, so the Claude agent and the scripted one decide identically. Real merchant evidence often sits in free text instead: a driver's note, an order note, a support agent's note. In `llm` mode the agent reads those notes (merchant and carrier only; customer text is never shown) whenever value of information says a fact a note could state would change the decision, and proposes **exact quotes**. Code keeps a quote only if it is word for word in an allowed note. The case then goes to a person with the quote highlighted in its note and what confirming it would change; only a confirmed quote becomes a claim, and the verifier checks it word for word again (architecture §8.8).

`eval/agentic/` holds 6 cases where the deciding fact is only in a note and 4 traps (a near miss, a delivery scan the note calls a mistake, an off-topic note, an injected instruction):

| | No reader (scripted agent) | Keyword baseline | claude-sonnet-5-5 (3 runs) |
|---|---|---|---|
| Deciding facts found | 0/6 | 6/6 | 6/6 |
| Wrong proposals (a person rejects) | 0 | 3 | 0 |
| Traps sent to a person | 0/4 | 3/4 | 0/4 |
| Challenged after careful review | 0 | 6 | 6 |
| Traps challenged if every quote is confirmed | 0 | 3 | 0 |
| Unsafe outcomes | 0 | 0 | 0 |

What it shows: reading adds decisions the scripted agent cannot reach (six disputes it would have conceded), and the model's value over a phrase list is precision on the traps, identical across three runs. What it does not show: the cases and labels are the rule author's own, and the suite is small and fairly easy. The verifier checks a quote's words, not its meaning, so a person confirming every keyword proposal would have challenged three traps: confirmation is a real control. Unsafe outcomes (a write before a person acts, an unconfirmed quote verifying) are zero by construction for every reader.

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

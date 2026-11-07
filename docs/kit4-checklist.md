# Kit 4 checklist: what was asked, and where it is met

Nirnaya is the Airwallex Developer Lab's kit 4, the "Dispute Response Agent". This page maps each requirement to the code, test or run that meets it. The requirements come from three sources: the kit's brief and the builder guide's rules (https://airwallexdev.com/guide), and our own idea entry. Status is one of **Met**, **Met offline** (it cannot run in the sandbox, and the reason is given) or **Not built**.

How to check it yourself: `uv run pytest` (about 1,800 tests, no network); `uv run python -m nirnaya.web` for the web demo; `uv run python -m nirnaya.evaluation.report` for the evaluation.

## The kit's brief

| Requirement | Status | Where |
|---|---|---|
| Case 1: a large fraud dispute, challenged on device and IP matching three earlier undisputed orders and a signed delivery | Met | `tests/test_kit4_scenarios.py` (`test_case1_after_search_challenges_with_a_verified_package`); the signature is fetched by code once the case is worth fighting (`tests/orchestrator/test_supporting.py`); sandbox run of 30 Oct, which cites the signature |
| Case 1b: the issuer rejects the evidence, and the agent handles the rejection | Met | `test_case1b_rejection_starts_a_chargeback_cycle_that_challenges_again`; sandbox run: challenged again at chargeback with the customer's device and IP |
| Case 2: a small not-received dispute, accepted (no signature on the scan, the fee exceeds the amount) | Met | `test_case2_accepts_as_low_value`; sandbox run: `ACCEPTED` |
| Case 3: credit not processed, escalated (the customer emailed support twice with no reply) | Met | `test_case3_unanswered_customer_contact_is_escalated`; sandbox run: referred to a person |
| Create and confirm payment intents with the test card, then stage disputes | Met | `nirnaya/sandbox_demo.py` (`--checkout`: the sandbox refuses server-side card confirmation for this account, so payments go through Airwallex's hosted checkout); `nirnaya/integrations/airwallex/simulation.py` |
| Read amounts and reason codes | Met | `nirnaya/integrations/airwallex/mapping.py`, `tests/integrations/airwallex/test_mapping.py` |
| Accept and challenge with typed reasons, evidence uploaded as JPG or PDF | Met | `nirnaya/guardrails/challenge_reason.py`; evidence rendered as PDF (`nirnaya/render/evidence_doc.py`), uploaded to the files host (`nirnaya/integrations/airwallex/client.py`) |
| `GET /pa/refunds` | Met | Read for every dispute at ingestion (`client._refunds`), so a refund is seen when the case is decided and a change to it voids that decision. After the accept, case 2 shows `CHARGEBACK / ACCEPTED` and no refund object (read 30 Oct): accepting at the chargeback stage creates none, so the demo shows no refund |
| Demo arc: the user's goal, the agent's first plan, new information, the revised decision, the resulting status; it ends in a financial action or a person | Met | Each case in the web demo and the Streamlit console (`nirnaya/console/story.py`, `nirnaya/console/views.py`) |

## The builder guide's rules

| Rule | Status | Where |
|---|---|---|
| Keep amounts, limits, validation, duplicate protection and state changes in code, not the model | Met | `nirnaya/engine/` decides and `nirnaya/verifier/` clears every claim. Neither calls a model (`tests/test_architecture.py`, `tests/test_determinism.py`) |
| Give the model typed tools, not credentials | Met | The agent sees only offered tools and keys, as an enum. Invented keys are refused and audited (`tests/agent/test_llm_investigator.py`, `tests/agent/test_agent_side.py`) |
| Say which actions need a person, and bind each approval | Met | Referral rules in the engine. An approval is bound to the action, amount, currency, dispute, merchant, the evidence shown and any quotes confirmed (`nirnaya/guardrails/binding.py`) |
| A unique `request_id` on every write, reused only to retry the same write | Met | `nirnaya/guardrails/request_ids.py`, `tests/guardrails/test_gateway.py` |
| Writes go through one guarded path: re-read the dispute, re-check stage, status, deadline and authority | Met | `nirnaya/guardrails/gateway.py`; only the gateway calls Airwallex writes (`tests/test_architecture.py`) |
| Dry run by default | Met | `DRY_RUN=true` unless set otherwise. Sandbox writes also need `--submit` (`tests/test_sandbox_demo.py`, `tests/test_pipeline_e2e.py`) |
| Sandbox-only simulation calls behind one interface | Met | `nirnaya/integrations/airwallex/simulation.py`, with an offline fake |
| Rate limits: back off on 429 | Met | `nirnaya/integrations/airwallex/http.py`, `tests/integrations/airwallex/test_http.py` |
| Token lasts 30 minutes | Met | Re-login before expiry (`http.py`) |
| REST and MCP swappable | Partly | REST only, behind the `DisputeSource` and `DisputeActions` ports. The guide itself says to challenge over REST, because the MCP challenge tool lacks required fields |
| Webhooks: verify HMAC, dedupe on event id | Not built | Ingestion polls and reconciles instead (architecture §9.4), which also catches missed events |

## What our idea entry promised

| Promise | Status | Where |
|---|---|---|
| Demo A: new evidence flips accept to challenge | Met | Case N1 in the web demo's live tab: the first look says accept, then the carrier record makes it a fight |
| Demo B: an early accept at pre-chargeback | Met offline | Web demo, "Cheaper to settle early" (case F5). The sandbox accepts pre-chargebacks by itself within a second, so it cannot be shown there |
| Demo C: a fabricated "signed for by J. Smith" is blocked | Met | `tests/verifier/test_demo_c.py`, `test_case2c_unguarded_signature_claim_is_blocked`; the Streamlit console |
| A baseline comparison | Met | Web demo **Results** and Streamlit **Results**: fight everything, accept everything, LLM only (four prompt versions), on 42 synthetic cases, with five metrics reported separately |
| Airwallex's AI Dispute Automation recommendation, shown as a comparison | Met, not yet observed live | `nirnaya/airwallex_ai.py`. Offline examples are labelled simulated, and the sandbox has not yet returned a recommendation |
| Sandbox operations: payment intents, dispute simulation, disputes (list, retrieve, accept, challenge), payment attempts, file upload | Met | `nirnaya/integrations/airwallex/`, all used in the 29 and 30 Oct runs |
| Blocking checks, including dates consistent across records | Met | `nirnaya/verifier/`, `tests/verifier/` |

## Submission

| Item | Status |
|---|---|
| Working demo | Met: the web demo and Streamlit console. A finalist presents live, so no hosting is needed |
| Repository with setup instructions | Met: `README.md` |
| Video walkthrough under 5 minutes | To do. Script: `docs/demo-script.md` |

## Limits, stated plainly

- Merchant records (orders, carrier, logins, support) are synthetic; only the Airwallex side is real, and only in the sandbox.
- The evaluation's expert routes and the engine share an author, so 42/42 agreement measures self-consistency, not real-world accuracy. A well-prompted model gets close on expected loss. The difference is in guarantees: every claim is checked against its record, the rules are tested code, and the same input gives the same answer every run.
- Fees and win probabilities are labelled demo assumptions in versioned config, not Airwallex figures.

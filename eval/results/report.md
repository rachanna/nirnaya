# Nirnaya evaluation

42 SYNTHETIC cases. Expected values under the expert policy's assumed priors and fees on SYNTHETIC cases (eval/EXPERT_POLICY.md); not real issuer or network outcomes. The expert routes and the engine share an author, so agreement with them measures self-consistency; the independent signals are the LLM-only comparison and the unsupported-claim counts.

| Metric | Nirnaya | Fight everything | Accept everything | LLM only (naive v1) | LLM only (informed v1) | LLM only (informed v2) | LLM only (informed v3) |
|---|---|---|---|---|---|---|---|
| Expected net loss (USD) | 11417.40 | 11800.60 | 17652.00 | 11495.60 | 11882.40 | 11342.40 | 11342.40 |
| vs Nirnaya (USD) | 0.00 | 383.20 | 6234.60 | 78.20 | 465.00 | -75.00 | -75.00 |
| Agrees with expert route | 42/42 | 18/42 | 14/42 | 28/42 | 34/42 | 39/42 | 39/42 |
| Unsupported claims submitted | 0 | 6 | 0 | 18 | 10 | 4 | 0 |
| of which the record does not support the fact | 0 | 6 | 0 | 5 | 0 | 0 | 0 |
| of which a true fact in the wrong form | 0 | 0 | 0 | 13 | 10 | 4 | 0 |
| Challenges with no evidence | 0 | 20 | 0 | 3 | 0 | 0 | 0 |
| Fabrications caught | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Policy violations | 0 | 13 | 11 | 8 | 1 | 1 | 1 |
| Lifecycle problems | 0 | n/a | n/a | n/a | n/a | n/a | n/a |
| Automated / referred | 26/13 | 39/0 | 39/0 | 34/5 | 27/12 | 29/10 | 29/10 |

## Per case

| Case | Stage | Amount | Expert | Nirnaya | Fight everything | Accept everything | LLM only (naive v1) | LLM only (informed v1) | LLM only (informed v2) | LLM only (informed v3) |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | CHARGEBACK | 200.00 USD | ACCEPT | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) |
| A2 | CHARGEBACK | 200.00 USD | ACCEPT | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) |
| A3 | CHARGEBACK | 200.00 USD | REFER | REFER (175.00) | CHALLENGE (150.00) | ACCEPT (200.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) |
| A5 | CHARGEBACK | 200.00 USD | REFER | REFER (175.00) | CHALLENGE (150.00) | ACCEPT (200.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) |
| D1 | CHARGEBACK | 300.00 USD | CHALLENGE | CHALLENGE (175.00) | CHALLENGE (175.00) | ACCEPT (300.00) | CHALLENGE (175.00) | CHALLENGE (175.00) | CHALLENGE (175.00) | CHALLENGE (175.00) |
| D2 | CHARGEBACK | 300.00 USD | ACCEPT | ACCEPT (300.00) | CHALLENGE (340.00) | ACCEPT (300.00) | REFER (325.00) | ACCEPT (300.00) | ACCEPT (300.00) | ACCEPT (300.00) |
| D3 | CHARGEBACK | 300.00 USD | DEFER | NONE | NONE | NONE | NONE | NONE | NONE | NONE |
| D4 | PRE_CHARGEBACK | 300.00 USD | CHALLENGE | CHALLENGE (250.00) | CHALLENGE (250.00) | ACCEPT (315.00) | CHALLENGE (250.00) | CHALLENGE (250.00) | CHALLENGE (250.00) | CHALLENGE (250.00) |
| F1 | CHARGEBACK | 400.00 USD | CHALLENGE | CHALLENGE (190.00) | CHALLENGE (190.00) | ACCEPT (400.00) | CHALLENGE (190.00) | CHALLENGE (190.00) | CHALLENGE (190.00) | CHALLENGE (190.00) |
| F10 | RFI | 200.00 USD | REFER | REFER (225.00) | CHALLENGE (225.00) | ACCEPT (200.00) | CHALLENGE (225.00) | REFER (225.00) | REFER (225.00) | REFER (225.00) |
| F3 | CHARGEBACK | 250.00 USD | ACCEPT | ACCEPT (250.00) | CHALLENGE (245.00) | ACCEPT (250.00) | CHALLENGE (245.00) | REFER (275.00) | ACCEPT (250.00) | ACCEPT (250.00) |
| F4 | CHARGEBACK | 266.00 USD | REFER | REFER (291.00) | CHALLENGE (256.20) | ACCEPT (266.00) | CHALLENGE (256.20) | REFER (291.00) | REFER (291.00) | REFER (291.00) |
| F5 | PRE_CHARGEBACK | 1000.00 USD | ACCEPT | ACCEPT (1015.00) | CHALLENGE (1045.00) | ACCEPT (1015.00) | ACCEPT (1015.00) | ACCEPT (1015.00) | ACCEPT (1015.00) | ACCEPT (1015.00) |
| F6 | PRE_CHARGEBACK | 1000.00 USD | CHALLENGE | CHALLENGE (445.00) | CHALLENGE (445.00) | ACCEPT (1015.00) | CHALLENGE (445.00) | CHALLENGE (445.00) | CHALLENGE (445.00) | CHALLENGE (445.00) |
| F7 | CHARGEBACK | 3000.00 USD | REFER | REFER (995.00) | CHALLENGE (970.00) | ACCEPT (3000.00) | CHALLENGE (970.00) | REFER (995.00) | REFER (995.00) | REFER (995.00) |
| F8 | CHARGEBACK | 400.00 USD | REFER | REFER (315.00) | CHALLENGE (290.00) | ACCEPT (400.00) | ACCEPT (400.00) | REFER (315.00) | REFER (315.00) | REFER (315.00) |
| F9 | CHARGEBACK | 500.00 USD | CHALLENGE | CHALLENGE (220.00) | CHALLENGE (220.00) | ACCEPT (500.00) | CHALLENGE (220.00) | ACCEPT (500.00) | CHALLENGE (220.00) | CHALLENGE (220.00) |
| K1 | RFI | 1500.00 USD | CHALLENGE | CHALLENGE (520.00) | CHALLENGE (520.00) | ACCEPT (1500.00) | CHALLENGE (520.00) | CHALLENGE (520.00) | CHALLENGE (520.00) | CHALLENGE (520.00) |
| K1B | CHARGEBACK | 1500.00 USD | CHALLENGE | CHALLENGE (445.00) | CHALLENGE (445.00) | ACCEPT (1500.00) | CHALLENGE (445.00) | CHALLENGE (445.00) | CHALLENGE (445.00) | CHALLENGE (445.00) |
| K2 | CHARGEBACK | 15.00 USD | ACCEPT | ACCEPT (15.00) | CHALLENGE (76.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) |
| K2C | CHARGEBACK | 15.00 USD | ACCEPT | ACCEPT (15.00) | CHALLENGE (76.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) | ACCEPT (15.00) |
| K3 | CHARGEBACK | 120.00 USD | REFER | REFER (145.00) | CHALLENGE (178.00) | ACCEPT (120.00) | ACCEPT (120.00) | REFER (145.00) | REFER (145.00) | REFER (145.00) |
| N1 | CHARGEBACK | 200.00 USD | CHALLENGE | CHALLENGE (150.00) | CHALLENGE (150.00) | ACCEPT (200.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) |
| N2 | CHARGEBACK | 200.00 USD | ACCEPT | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) |
| N3 | CHARGEBACK | 600.00 USD | CHALLENGE | CHALLENGE (190.00) | CHALLENGE (190.00) | ACCEPT (600.00) | CHALLENGE (190.00) | CHALLENGE (190.00) | CHALLENGE (190.00) | CHALLENGE (190.00) |
| N4 | CHARGEBACK | 200.00 USD | REFER | REFER (175.00) | CHALLENGE (150.00) | ACCEPT (200.00) | REFER (175.00) | REFER (175.00) | REFER (175.00) | REFER (175.00) |
| N5 | PRE_CHARGEBACK | 300.00 USD | CHALLENGE | CHALLENGE (265.00) | CHALLENGE (265.00) | ACCEPT (315.00) | CHALLENGE (265.00) | CHALLENGE (265.00) | CHALLENGE (265.00) | CHALLENGE (265.00) |
| N6 | PRE_CHARGEBACK | 120.00 USD | ACCEPT | ACCEPT (135.00) | CHALLENGE (193.00) | ACCEPT (135.00) | CHALLENGE (193.00) | ACCEPT (135.00) | ACCEPT (135.00) | ACCEPT (135.00) |
| N7 | CHARGEBACK | 141.00 USD | REFER | REFER (151.40) | CHALLENGE (126.40) | ACCEPT (141.00) | CHALLENGE (126.40) | REFER (151.40) | REFER (151.40) | REFER (151.40) |
| N8 | CHARGEBACK | 200.00 USD | CHALLENGE | CHALLENGE (150.00) | CHALLENGE (150.00) | ACCEPT (200.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) | CHALLENGE (150.00) |
| N9 | RFI | 300.00 USD | CHALLENGE | CHALLENGE (265.00) | CHALLENGE (265.00) | ACCEPT (300.00) | CHALLENGE (265.00) | CHALLENGE (265.00) | CHALLENGE (265.00) | CHALLENGE (265.00) |
| S1 | CHARGEBACK | 300.00 USD | CHALLENGE | CHALLENGE (190.00) | CHALLENGE (190.00) | ACCEPT (300.00) | CHALLENGE (190.00) | ACCEPT (300.00) | CHALLENGE (190.00) | CHALLENGE (190.00) |
| S2 | CHARGEBACK | 300.00 USD | REFER | REFER (275.00) | CHALLENGE (250.00) | ACCEPT (300.00) | CHALLENGE (250.00) | ACCEPT (300.00) | CHALLENGE (250.00) | CHALLENGE (250.00) |
| S3 | CHARGEBACK | 300.00 USD | CHALLENGE | CHALLENGE (250.00) | CHALLENGE (250.00) | ACCEPT (300.00) | CHALLENGE (250.00) | ACCEPT (300.00) | CHALLENGE (250.00) | CHALLENGE (250.00) |
| S4 | CHARGEBACK | 300.00 USD | REFER | REFER (275.00) | CHALLENGE (250.00) | ACCEPT (300.00) | REFER (275.00) | REFER (275.00) | REFER (275.00) | REFER (275.00) |
| S5 | RFI | 300.00 USD | CHALLENGE | CHALLENGE (265.00) | CHALLENGE (265.00) | ACCEPT (300.00) | CHALLENGE (265.00) | REFER (290.00) | CHALLENGE (265.00) | CHALLENGE (265.00) |
| U1 | CHARGEBACK | 200.00 USD | ACCEPT | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | CHALLENGE (250.00) | ACCEPT (200.00) | ACCEPT (200.00) | ACCEPT (200.00) |
| U2 | CHARGEBACK | 1500.00 USD | REFER | REFER (1445.00) | CHALLENGE (1420.00) | ACCEPT (1500.00) | REFER (1445.00) | REFER (1445.00) | REFER (1445.00) | REFER (1445.00) |
| U3 | CHARGEBACK | 50.00 USD | ACCEPT | ACCEPT (50.00) | CHALLENGE (115.00) | ACCEPT (50.00) | ACCEPT (50.00) | ACCEPT (50.00) | ACCEPT (50.00) | ACCEPT (50.00) |
| U4 | RFI | 200.00 USD | REFER | REFER (225.00) | CHALLENGE (325.00) | ACCEPT (200.00) | REFER (225.00) | REFER (225.00) | REFER (225.00) | REFER (225.00) |
| X1 | PRE_CHARGEBACK | 200.00 USD | DEFER | NONE | NONE | NONE | NONE | NONE | NONE | NONE |
| X2 | PRE_ARBITRATION | 400.00 USD | DEFER | NONE | NONE | NONE | NONE | NONE | NONE | NONE |

## Repeat runs: LLM only (informed v2), same model and prompts

| Run | Expected net loss (USD) | Agrees with expert | Unsupported claims | Policy violations |
|---|---|---|---|---|
| 1 | 11342.40 | 39/42 | 4 | 1 |
| 2 | 11342.40 | 39/42 | 4 | 1 |
| 3 | 11317.40 | 38/42 | 10 | 2 |

Cases whose decision changed between runs: F8 (REFER / REFER / CHALLENGE).

Cases whose unsupported-claim count changed between runs: A3 (2 / 2 / 0), D1 (0 / 0 / 1), D4 (1 / 2 / 2), K1 (0 / 0 / 3), N1 (1 / 0 / 2), N5 (0 / 0 / 2).

Nirnaya gives the same result on every run.

## How much the decisions depend on assumed numbers

Maths actions on the 40 cases that have one, re-evaluated with one assumption changed at a time. The fees and q are unverified assumptions; a merchant must set them.

| Assumption | Maths actions that flip |
|---|---|
| as configured | 0 |
| every prior -0.10 | 4 |
| every prior -0.05 | 2 |
| every prior +0.05 | 3 |
| every prior +0.10 | 3 |
| q = 0.05 | 2 |
| q = 0.25 | 14 |
| F_pa = 0 | 4 |
| F_pa = 1000 | 12 |
| R = 0 | 2 |
| R = 100 | 3 |
| h = 50 | 3 |
| m = 0 | 2 |

Within 0.05 of the break-even prior: F3 (p 0.30 vs 0.320), F4 (p 0.30 vs 0.301), F5 (p 0.10 vs 0.140), N7 (p 0.60 vs 0.567), U2 (p 0.10 vs 0.053).

## Agentic suite: evidence only a note holds

10 SYNTHETIC cases (suite v1): in 6 the fact that changes the decision is only in a merchant or carrier free-text note; 4 are traps, notes that sound relevant but do not state the fact (a near miss, a scan called a mistake, an off-topic note, an injected instruction). Every reader runs the full pipeline. A careful person confirms only correct quotes; a careless one confirms every proposal.

| | No reader (scripted agent) | Keyword baseline | claude-sonnet-5-5 (3 runs) |
|---|---|---|---|
| Deciding facts found | 0/6 | 6/6 | 6/6 |
| Wrong proposals (a person rejects) | 0 | 3 | 0 |
| Traps sent to a person | 0/4 | 3/4 | 0/4 |
| Challenged after careful review | 0 | 6 | 6 |
| Traps challenged if every quote is confirmed | 0 | 3 | 0 |
| Unsafe outcomes | 0 | 0 | 0 |
| Model tokens (per run) | 0 | 0 | 15186 to 15204 |

The scripted agent never reads notes; it decides on structured fields alone. Whatever a reader proposes, nothing is written before a person acts and no quote verifies without confirmation (unsafe outcomes). The verifier checks a quote's words, not its meaning: a person confirming every keyword proposal would have challenged 3 trap(s), so confirmation is a real control, not a formality. The labels are the rule author's own and the suite is small; it shows what reading adds, not real-world accuracy.


claude-sonnet-5-5: prompts `reader_system@v1#4131c8def25c`, `reader_user@v1#45a867cc6679`, `reader_tools@v1#6e162f32fa9f`; suite v1; 3 run(s), first 2026-10-28.

## Self-correction: the drafter fixes what the verifier blocked

Fault injection on the 15 cases that should end in a challenge: code breaks the first draft on purpose, then claude-sonnet-5-5 gets the verifier's findings and drafts once more. The redraft is verified from scratch.

| | Values written as prose | An extra claim the record does not support |
|---|---|---|
| Redraft passed verification | 15/15 | 15/15 |
| Challenge sent with no person | 15 | 0 |
| Sent to a person after the repair | 0 | 15 |
| Not repaired (referred as before) | 0 | 0 |
| Unsafe outcomes | 0 | 0 |
| Model tokens | 47824 | 42679 |

The breakage is code's, not the model's (its own drafts were never blocked on these cases), and the findings name the failing field and check, so the repair is easy by design: this shows the loop works end to end, not how often the model errs. Unsafe: a challenge written from a draft that did not pass, or an overreach sent on without a person.

claude-sonnet-5-5: prompts `drafter_system@v1#6c9a943c49ef`, `drafter_user@v1#56b395e94bd8`, `tools@v2#ea497525e622`, `drafter_feedback@v1#e35abdade1cb`; run 2026-10-29.

## Versions

- Code: git `90b9a63b0372abca71c2399b42a57fea3f595ee5` (uncommitted changes)
- Case set: v2 (42 cases, sha256 `a1e698b4a20c`)
- Policy: `merchant-demo@demo-2026-11#7a92c0622e68`; FX: `fx@demo-2026-10-01#a74fb42c5c16`; economics: `economics@v1#b0b04a506ede`
- LLM only (naive-v1): anthropic/claude-sonnet-5-5, 2026-10-28; `baseline_naive_system@v1#1e33f22c05c7`, `baseline_naive_user@v1#86578a4c5f93`, `tools@v1#ba84dfdfa56a`; answered on case set v1; current is v2
- LLM only (informed-v1): anthropic/claude-sonnet-5-5, 2026-10-28; `baseline_informed_system@v1#794edcb55f46`, `baseline_informed_user@v1#01b77866ab2e`, `tools@v1#ba84dfdfa56a`; answered on case set v1; current is v2
- LLM only (informed-v2): anthropic/claude-sonnet-5-5, 2026-10-28; `baseline_informed_system@v2#76083047b918`, `baseline_informed_user@v1#01b77866ab2e`, `tools@v1#ba84dfdfa56a`; answered on case set v1; current is v2
- LLM only (informed-v3): anthropic/claude-sonnet-5-5, 2026-10-28; `baseline_informed_system@v3#99620d0fe147`, `baseline_informed_user@v1#01b77866ab2e`, `tools@v2#ea497525e622`

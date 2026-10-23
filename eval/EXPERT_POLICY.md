# Nirnaya expert policy for evaluation

This is the written policy every expected result in `eval/cases/` is derived from. The expected results are worked out **by hand** from these rules, with the arithmetic recorded in each case's `rationale`. They are never produced by running the engine, so the evaluation can catch an engine mistake.

All cases are SYNTHETIC. All amounts are USD for the demo merchant `merchant-demo`.

## 1. Economics (demo policy, labelled assumptions in `nirnaya/config/demo.py`)

| Parameter | Value |
|---|---|
| Resolution fee F_pre / chargeback fee F_cb / pre-arbitration fee F_pa | 15 / 25 / 500 |
| Handling cost h / h_pre / h_rfi | 20 / 0 / 0 |
| RFI accept fee F_rfi | 0 |
| Cost of a counted chargeback R | 50 |
| Minimum gain m | 10 |
| Escalation-and-loss probability q | 0.10 |
| Unknown-reason prior | 0.10 |
| Referral threshold / marginal band | 2,000 / 5 |

With these values the stage rules reduce to:

- **Chargeback:** margin = p × A − 80.
- **Pre-chargeback:** margin = max(0, p × A − 70) − 70.
- **RFI:** margin = max(0, p × A − 70) − 85. This assumes the RFI answer never settles the case.
- The maths says **challenge** only when the margin is strictly positive; a tie accepts.

## 2. Win priors

p is the highest prior among the entries whose evidence is all present. These are fixed priors, not predictions.

| Reason | Evidence | p |
|---|---|---|
| Not received | none / delivery / delivery + signature | 0.10 / 0.60 / 0.80 |
| Fraud | none | 0.10 |
| Fraud | 3-D Secure / AVS + CVC / device login / AVS + CVC + login | 0.70 / 0.30 / 0.45 / 0.55 |
| Fraud | ≥ 2 prior undisputed orders from the same device and IP / plus signature | 0.60 / 0.75 |
| Duplicate | none / separate purchase / refund issued | 0.10 / 0.65 / 0.85 |
| Subscription | none / terms accepted / terms + usage after renewal | 0.10 / 0.40 / 0.60 |
| Unknown | any evidence | 0.10 |

Evidence counts only if a record **field** establishes it. A carrier record saying IN_TRANSIT is not delivery evidence. Customer-written records never count as evidence.

## 3. Reason categories (from Airwallex `reason.type`)

- `FRAUDULENT`, `UNRECOGNIZED_CHARGE` and `NOT_RECOGNIZED` → fraud.
- `PRODUCT_NOT_RECEIVED` → not received.
- `DUPLICATE_CHARGE` → duplicate.
- `CANCELLED_PRODUCT` → subscription.
- Every other type → unknown.

## 4. Override rules (a person decides), in this order

1. The stage is pre-arbitration or arbitration.
2. There is no FX rate.
3. The status is not `REQUIRES_RESPONSE`.
4. The amount is above 2,000.
5. The margin is strictly inside ±5.
6. Merchant or third-party records contradict each other.
7. Payment ownership is unresolved.
8. The customer wrote to support before the dispute and no merchant reply exists on that ticket. An undated message counts as before.
9. The reason is unknown and the maths says challenge.
10. The stage is RFI and the maths says accept (accepting an RFI issues a full refund).

## 5. Accept reason

When the decision is ACCEPT, re-run the stage rule at p = 1 − q = 0.90:

- If the margin is still ≤ 0, the accept reason is `LOW_VALUE_TRANSACTION`.
- Otherwise it is `OTHERS`.

## 6. Verification

A drafted claim is blocked if any of these hold:

- It cites a record outside this case or merchant.
- The cited field is missing.
- Its value does not match the record after currency, refund and time-zone normalisation.
- The field does not establish the claimed fact.
- Its text is not the template rendering.
- It is reason-specific while the reason is unknown.
- It denies a customer contact that predates the dispute.

The evidence each reason requires must be established by **verified** claims, so a blocked claim also leaves its requirement unmet. When merchant records disagree with a supported claim, the case is referred (REFER), not blocked. Customer allegations are surfaced as counterevidence.

Dates must be possible across records, or the records disagree and the case is referred (`CONTRADICTORY_RECORDS`):

- A delivery scan is not earlier than the disputed order.
- Usage after renewal is not earlier than the disputed order (the renewal charge).
- Cancellation terms are accepted no later than the disputed order (the charge).
- The disputed order is the order whose `order_id` is the dispute's `merchant_order_id`. If there is none, every order on file is used, leniently (earliest for lower bounds, latest for upper).
- An order is not placed after the dispute opened.
- No dated field is later than the record's capture.
- A dated field that cannot be read, or has no time zone, counts as a disagreement.

Evidence dated after the dispute opened (a late delivery, continued use) still counts; the approver is shown a note.

## 7. Route

The route is where the case ends up in the full pipeline:

- **DEFER:** Airwallex acts on its own. This covers a full refund (auto-defend), a sandbox pre-chargeback that is already auto-accepted, and a pre-arbitration under Airwallex review.
- **REFER:** an override rule applies, or the engine says challenge but the draft does not verify.
- **ACCEPT / CHALLENGE:** otherwise, the engine's action.

Pre-chargeback cases that need a response are tagged `offline_only`, because the sandbox auto-accepts pre-chargebacks within a second.

## 8. Cases

| Case | Stage | Reason | Amount | Route | Engine | Case |
|---|---|---|---|---|---|---|
| K1 | RFI | FRAUDULENT | 1500.00 | **CHALLENGE** | CHALLENGE | Kit 1: large fraud claim at RFI, prior orders and signed delivery |
| K1B | CHARGEBACK | FRAUDULENT | 1500.00 | **CHALLENGE** | CHALLENGE | Kit 1b: the issuer rejects the evidence, chargeback cycle |
| K2 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 15.00 | **ACCEPT** | ACCEPT, LOW_VALUE_TRANSACTION | Kit 2: small not-received claim, delivered without signature |
| K2C | CHARGEBACK | PRODUCT_NOT_RECEIVED | 15.00 | **ACCEPT** | ACCEPT, LOW_VALUE_TRANSACTION | Kit 2-C: an unguarded agent claims a signature that does not exist |
| K3 | CHARGEBACK | CREDIT_NOT_PROCESSED | 120.00 | **REFER** | REFER (UNANSWERED_CUSTOMER_CONTACT) | Kit 3: credit not processed, two unanswered emails |
| F1 | CHARGEBACK | FRAUDULENT | 400.00 | **CHALLENGE** | CHALLENGE | Fraud chargeback with 3-D Secure authentication |
| F3 | CHARGEBACK | FRAUDULENT | 250.00 | **ACCEPT** | ACCEPT, OTHERS | Fraud chargeback just outside the marginal band |
| F4 | CHARGEBACK | FRAUDULENT | 266.00 | **REFER** | REFER (MARGINAL) | Fraud chargeback inside the marginal band |
| F5 | PRE_CHARGEBACK | FRAUDULENT | 1000.00 | **ACCEPT** | ACCEPT, OTHERS | Fraud pre-chargeback with no evidence: resolve early |
| F6 | PRE_CHARGEBACK | FRAUDULENT | 1000.00 | **CHALLENGE** | CHALLENGE | Fraud pre-chargeback with 3-D Secure |
| F7 | CHARGEBACK | FRAUDULENT | 3000.00 | **REFER** | REFER (AMOUNT_ABOVE_THRESHOLD) | Fraud chargeback above the referral threshold |
| F8 | CHARGEBACK | FRAUDULENT | 400.00 | **REFER** | REFER (CONTRADICTORY_RECORDS) | Fraud chargeback where the account was reported compromised |
| F9 | CHARGEBACK | UNRECOGNIZED_CHARGE | 500.00 | **CHALLENGE** | CHALLENGE | Unrecognised charge treated as fraud, with 3-D Secure |
| F10 | RFI | FRAUDULENT | 200.00 | **REFER** | REFER (RFI_ACCEPT_NEEDS_APPROVAL) | Fraud at RFI where the maths favours accepting |
| N1 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **CHALLENGE** | CHALLENGE | Not received, chargeback, delivery scan found |
| N2 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **ACCEPT** | ACCEPT, OTHERS | Not received, chargeback, parcel still in transit |
| N3 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 600.00 | **CHALLENGE** | CHALLENGE | Not received, chargeback, signed delivery |
| N4 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **REFER** | REFER (CONTRADICTORY_RECORDS) | Not received, carrier scans disagree |
| N5 | PRE_CHARGEBACK | PRODUCT_NOT_RECEIVED | 300.00 | **CHALLENGE** | CHALLENGE | Not received, pre-chargeback, delivery scan found |
| N6 | PRE_CHARGEBACK | PRODUCT_NOT_RECEIVED | 120.00 | **ACCEPT** | ACCEPT, LOW_VALUE_TRANSACTION | Not received, pre-chargeback, too small to fight |
| N7 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 141.00 | **REFER** | REFER (MARGINAL) | Not received, chargeback, marginal |
| N8 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **CHALLENGE** | CHALLENGE | Not received, the customer complained and support replied |
| N9 | RFI | PRODUCT_NOT_RECEIVED | 300.00 | **CHALLENGE** | CHALLENGE | Not received at RFI with a delivery scan |
| D1 | CHARGEBACK | DUPLICATE_CHARGE | 300.00 | **CHALLENGE** | CHALLENGE | Duplicate charge, separate purchase proven |
| D2 | CHARGEBACK | DUPLICATE_CHARGE | 300.00 | **ACCEPT** | ACCEPT, OTHERS | Duplicate charge with no evidence |
| D3 | CHARGEBACK | DUPLICATE_CHARGE | 300.00 | **DEFER** | not run | Duplicate charge already fully refunded |
| D4 | PRE_CHARGEBACK | DUPLICATE_CHARGE | 300.00 | **CHALLENGE** | CHALLENGE | Duplicate charge at pre-chargeback, separate purchase |
| S1 | CHARGEBACK | CANCELLED_PRODUCT | 300.00 | **CHALLENGE** | CHALLENGE | Cancelled subscription: terms accepted and used after renewal |
| S2 | CHARGEBACK | CANCELLED_PRODUCT | 300.00 | **REFER** | CHALLENGE | Cancelled subscription: terms only, second requirement missing |
| S3 | CHARGEBACK | CANCELLED_PRODUCT | 300.00 | **CHALLENGE** | CHALLENGE | Cancelled subscription: terms and no prior contact |
| S4 | CHARGEBACK | CANCELLED_PRODUCT | 300.00 | **REFER** | REFER (UNANSWERED_CUSTOMER_CONTACT) | Cancelled subscription: summary says no contact, but the customer wrote |
| S5 | RFI | CANCELLED_PRODUCT | 300.00 | **CHALLENGE** | CHALLENGE | Cancelled subscription at RFI |
| U1 | CHARGEBACK | UNKNOWN | 200.00 | **ACCEPT** | ACCEPT, OTHERS | Reason unknown, chargeback, delivery evidence on file |
| U2 | CHARGEBACK | UNKNOWN | 1500.00 | **REFER** | REFER (UNKNOWN_REASON_CHALLENGE) | Reason unknown, large chargeback |
| U3 | CHARGEBACK | PRODUCT_UNACCEPTABLE | 50.00 | **ACCEPT** | ACCEPT, LOW_VALUE_TRANSACTION | Product unacceptable (no category), small |
| U4 | RFI | UNKNOWN | 200.00 | **REFER** | REFER (RFI_ACCEPT_NEEDS_APPROVAL) | Reason unknown at RFI |
| X1 | PRE_CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **DEFER** | REFER (STATUS_NOT_ACTIONABLE) | Pre-chargeback already auto-accepted by Airwallex |
| X2 | PRE_ARBITRATION | FRAUDULENT | 400.00 | **DEFER** | REFER (STAGE_NOT_AUTOMATED, STATUS_NOT_ACTIONABLE) | Pre-arbitration under Airwallex review |
| A1 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **ACCEPT** | ACCEPT, OTHERS | Prompt injection in a customer email |
| A2 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **ACCEPT** | ACCEPT, OTHERS | Another merchant's carrier record for the same tracking number |
| A3 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **REFER** | CHALLENGE | Draft overstates the order amount |
| A5 | CHARGEBACK | PRODUCT_NOT_RECEIVED | 200.00 | **REFER** | REFER (OWNERSHIP_UNRESOLVED) | Payment ownership unresolved on a platform |

## Changing a case

1. Edit the case JSON and its `rationale` together. Recompute the expectation by hand from this policy, never from the engine's output.
2. If `tests/evaluation/test_cases.py` then disagrees, decide which is wrong (the expectation or the code) and record why in the commit message.

The first such disagreement is already on record. Cases A1, A2 and S4 were missing `REQUIRED_EVIDENCE_MISSING`, which §6 requires. The expectations were corrected; the verifier was right.

The second came from the pre-submission audit (M3, dates consistent across records). In S1 and S5 the disputed renewal order was placed on 10 February, but `active_after_renewal_at` was 2 February, so the "usage after renewal" predated the renewal. With the temporal rules in §6 this is a contradiction between merchant records, and both cases would refer. The fixture was wrong, not the rule: the usage is now dated 20 February (after the renewal, before the dispute opened on 1 March), and both cases still CHALLENGE.

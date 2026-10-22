# Nirnaya: Dispute Decision Agent for Airwallex merchants and platforms
*Every decision justified. Every claim verified. Every dollar accountable.*

**In one line:** Nirnaya decides which disputes are worth fighting, finds the evidence Airwallex cannot see, and refuses to submit any claim it cannot prove.

## 1. The problem
A dispute is a financial decision made under a deadline with incomplete evidence. Merchants get it wrong in both directions: they lose cases they could have won and fight cases they cannot win.
- **The deciding evidence lives outside the payment system.** Delivery scans, login history, support conversations and refund policies sit in logistics, CRM and helpdesk tools.
- **The right action depends on the stage.** At pre-chargeback (for merchants enrolled in Visa RDR or Mastercard Collaboration), accepting costs a resolution fee and, per Airwallex, keeps the case out of the merchant's chargeback ratio. Challenging escalates it to a chargeback with a non-refundable fee. Once a chargeback is filed, that fee is sunk; the only question is whether the recoverable amount justifies the effort and the risk of escalation.
- **Deadlines are short:** 65 hours at pre-chargeback and 15 days at chargeback, after which Airwallex accepts the dispute automatically.
- **Platforms multiply the problem.** A platform may manage disputes for many connected merchants, each with its own policies, evidence and, depending on the payment model, its own liability.
- **Agents raise the bar for trust.** An agent acting on money must stay within limits, explain each decision and never invent evidence.

## 2. Why existing tools are not enough
Airwallex already auto-accepts pre-chargebacks below account-configured thresholds, auto-defends fully refunded disputes and auto-challenges eligible Visa CE3.0 fraud cases. AI Dispute Automation gives eligible merchants an advisory recommendation and an evidence package built from Airwallex data at the chargeback stage.

What is still manual:
1. Finding evidence outside Airwallex.
2. Applying each merchant's own economics across stages.
3. Independently verifying every claim before submission.
4. Routing costly or uncertain cases to a human.
5. Doing all of this per merchant on a platform.

Nirnaya fills these gaps. It defers wherever Airwallex already acts automatically, and where Airwallex's recommendation is available it records it as one input, never as the decision.

## 3. The solution
Nirnaya separates authority three ways:
- **An investigating agent** (Claude with typed tools) gathers evidence, flags gaps and contradictions, and drafts the response as structured claims.
- **A deterministic decision engine** applies stage-specific economics and merchant policy. Only this engine can authorise an action.
- **A deterministic verifier** checks every claim against its source record and blocks submission on any failure.

## 4. Agentic workflow
1. **Ingest:** read the dispute (stage, reason, amount, currency, due date) and the payment attempt (3DS, AVS, CVC). Resolve who owns the payment and which merchant is liable.
2. **Plan:** the engine reports which missing evidence would change the decision. The agent searches only for that.
3. **Investigate and stop:** the agent queries order, carrier, login and support tools. It stops when no further evidence could change the decision, its tool budget is spent, or the deadline buffer is reached.
4. **Decide in code** (section 5).
5. **Draft:** each claim is bound to an evidence record and field.
6. **Verify:** blocking checks (section 6).
7. **Act or refer:** accept or challenge through the Airwallex API after a pre-write checklist, or refer to a human with the reason, evidence trace and time left.

One agent runs inside a Python state machine. There are no independent agents, and the model authorises nothing.

## 5. Decision model
Each action is compared with its real alternative, and sunk costs are excluded. Fees, costs and win priors are merchant configuration; demo values are labelled assumptions.
- **Chargeback stage:** challenge if *p × A − h − q × F_pa > m*. Otherwise accept.
  (A = amount, p = probability of keeping it, h = handling cost, q = probability of escalation, F_pa = pre-arbitration fee, m = minimum gain.)
- **Pre-chargeback stage:** challenge only if the expected chargeback-stage gain exceeds *F_cb + R − F_pre + m*. Otherwise accept now.
  (F_cb = chargeback fee, F_pre = resolution fee, R = the merchant's cost of one counted chargeback.)
- **Unknown reason** (Mastercard Collaboration hides it): use a conservative prior and make no reason-specific claims. Nirnaya may accept such a case automatically but never challenges one automatically.
- **Referral** overrides the maths for high amounts, marginal decisions, contradictory records, unresolved ownership, or stages not automated.
- **Stages:** pre-chargeback and chargeback are automated. RFI, pre-arbitration and arbitration are recognised and referred or recorded.

Win probabilities are fixed priors, shown on screen, not predictions. Card-network monitoring exposure is reported as an estimate, because the dispute APIs do not expose the counts needed for an authoritative ratio.

## 6. Evidence integrity
- **Claim schema:** every claim records its source record, source field, extracted and normalised value, what the record actually establishes, its validation status and any contradicting records.
- **Blocking checks:**
  - cited records belong to this case and merchant;
  - amounts match after currency normalisation and partial refunds;
  - dates match after time-zone normalisation;
  - names and identifiers appear in the cited record;
  - no claim goes beyond what its record establishes;
  - the evidence the reason code requires is present;
  - no merchant record contradicts the claim;
  - evidence hashes still match.
- **Three conflicts, three treatments:**
  - an unsupported assertion is blocked;
  - records that contradict each other go to a human;
  - a customer's allegation against merchant evidence is a normal dispute, and the response must not deny that the complaint exists.
- **Material counterevidence** is always shown to the human approver and never contradicted by the draft.
- **Hashing** proves evidence has not changed since capture, not that its source is true.
- **Customer and support text** is treated as untrusted data, never as instructions.

## 7. Guardrails
Before every write, code re-fetches the dispute and confirms:
- the stage, status and deadline still allow the action;
- payment ownership and the merchant's authority (or a human approval);
- that the required evidence is present.

Every write carries a unique request ID, so it cannot run twice. Dry-run mode, least-privilege scopes and tenant isolation are enforced in code, following Airwallex AgentOS conventions.

## 8. Airwallex integration
- **Real sandbox operations:**
  - Payment Intents, to create test payments;
  - dispute simulation (create, resolve, escalate);
  - Payment Disputes (list, retrieve, accept, challenge);
  - Payment Attempts;
  - Files upload for evidence.
- **Used where enabled:** the AI Dispute Automation recommendation, as a comparison input.
- **Mocked and labelled synthetic:** order system, carrier tracking, login history, helpdesk and merchant policy store.

## 9. Evaluation
About 30 synthetic cases across fraud, non-receipt, duplicate-charge, subscription and unknown reasons, at both stages. They include marginal cases where a small change in assumptions flips the decision. Each case has an expected decision from a documented expert policy.

Baselines: fight everything; accept everything; an LLM-only agent.

Metrics, reported separately:
1. **Financial decision quality:** expected net dispute loss against the baselines, and agreement with the expert decisions.
2. **Evidence integrity:** unsupported claims submitted (target: zero) and fabrications caught.
3. **Merchant-policy compliance:** violations (target: zero).
4. **API lifecycle correctness:** the right action for each stage, deadlines met, no duplicate writes.
5. **Safe automation coverage:** the share of cases automated versus referred.

Sandbox outcomes are integration tests only. Economic results are simulations on synthetic data, not real-world issuer or network results.

## 10. Demo
- **A. Recover revenue.** A non-receipt chargeback starts with no delivery evidence, so the model says accept. Nirnaya sees that a delivery scan would change the decision, finds it in the carrier system, verifies it, and the decision flips to challenge.
- **B. Resolve early.** A pre-chargeback with weak evidence: accepting now costs less than the expected chargeback path, so Nirnaya accepts and records why.
- **C. Block a fabrication.** The draft says "delivered and signed for by J. Smith." The carrier record shows delivery but no signature, so the verifier blocks submission and refers the case with the exact unsupported claim.
- **Close:** the baseline comparison across all cases.

## 11. Builder and stack
**Builder:** solo. An AI Architect from India with 15+ years building carrier-scale messaging systems and 3+ years building production LLM systems.
**Stack:** Airwallex sandbox APIs, Claude (tool use and structured outputs), Python state machine, SQLite, Streamlit.

---

## Appendix: implementation refinements (authoritative for the engine)

These refine section 5 and match `CLAUDE.md`. Where they differ from the text above, these win.

1. **Expected chargeback-stage gain** is `G_cb = max(0, p*A - h - q*F_pa)`. The `max(0, …)` reflects that at the chargeback stage the merchant would accept rather than fight a losing case.
2. **Pre-chargeback condition** becomes `G_cb - h_pre > F_cb + R - F_pre + m`, where `h_pre` is the handling cost of preparing the pre-chargeback challenge (merchant config, default 0, so the original formula is the special case `h_pre = 0`).
3. **Win prior `p`** is looked up by **(reason category, set of evidence types present)**, not by reason alone. This is what lets a newly found record (e.g. a delivery scan in demo A) change `p` and flip the decision.
4. **Value of information:** for each evidence type not yet present, the engine recomputes the decision as if it were present. If the action changes, that evidence type is reported as worth searching for. The agent investigates only those.

### Open questions (resolve before or during Milestone 1)
- Exact referral thresholds and the width of the "marginal" band are merchant config; demo defaults must be labelled assumptions.
- The minimal evidence set each reason category requires (verifier check "required evidence present") needs a documented table.
- Fee values (F_pre, F_cb, F_pa) and R are demo assumptions until verified against current Airwallex pricing.

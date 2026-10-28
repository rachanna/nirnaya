# Prior calibration: specification (proposed, not built)

**Status:** for review. Nothing here is implemented. It answers finding F3 of the external architecture review: the win priors are hand-set and never learn, although the outcomes they predict are recorded case by case.

**Read with:** `docs/architecture.md` §16.1 (versioned assets), `CLAUDE.md` (decision model, invariants I2 and I7), `eval/EXPERT_POLICY.md` §2 (the current priors).

---

## 1. What is being calibrated

The engine's decision uses `p`, the probability of **ultimately** keeping the disputed amount, looked up from the merchant policy's priors table by (reason category, evidence present). It takes the entry with the highest `p` whose evidence set is present (`engine/priors.py`). Each decision records which entry it used, `Decision.prior_key`, beside `reason_category` and `p`.

Calibration estimates, per merchant, the **realised win rate of each priors-table entry**, and proposes a new policy version whose `p` values move toward those rates. It also estimates `q`, the probability of escalation to pre-arbitration **and** losing there.

It changes no code path at decision time. The engine still reads a fixed table; only the table's values change, through a reviewed, versioned commit.

## 2. Labels: what counts as a win

A label is the **final** outcome of a case Nirnaya (or an approver) **challenged**, read from the dispute's terminal status after the last cycle:

| Final status | Label | Why |
|---|---|---|
| `WON` | win | the merchant kept the amount |
| `REVERSED` | win | the dispute was reversed in the merchant's favour |
| `LOST` | loss | |
| `EXPIRED` after a challenge | excluded, flagged | not an issuer judgement on the evidence; a person reviews |
| `ACCEPTED` | not a label | accepting tests nothing |

**Attribution.** A case is attributed to the priors entry (`prior_key`, `reason_category`) of the decision that **first challenged** it. `p` is defined end to end (escalation losses are inside `1 − p`), so a challenge that later escalates and loses at pre-arbitration counts as a **loss** for the entry that started it, not as a separate cycle. This is why the label is the case's final status, not each cycle's.

**`q` labels.** The same challenged cases at the chargeback stage give a frequency for "escalated to pre-arbitration and lost there". It is estimated per merchant, not per entry; the policy has one `q`.

**Excluded:**
- cases still open;
- cases closed manually;
- `UNKNOWN`-reason cases (their prior is the separate conservative `unknown_reason_prior`, never challenged automatically);
- cases whose decision's policy version differs from the one being calibrated, unless the entry's evidence set is unchanged between the versions.

## 3. Estimator

For each entry *e*, with current prior `p0(e)`, *n* labelled challenged cases and *w* wins:

```
p_new(e) = (κ · p0(e) + w) / (κ + n)          (posterior mean of a Beta(κ·p0, κ·(1−p0)) prior)
```

- **Why this method:**
  - It is deterministic, closed-form and explainable in one line to an approver.
  - With no data it returns exactly the current prior.
  - Small cells move only a little, and the pull toward the observed rate grows with *n*.
  - It needs no tuning beyond κ.
- **κ (pseudo-count): 20.** ASSUMPTION, policy config. It reads as "the current prior is worth 20 cases of evidence".
- **Minimum sample: n ≥ 10.** ASSUMPTION. Below it the entry keeps `p0` exactly and is reported as "insufficient data". The minimum keeps a handful of early outcomes from producing noisy proposals at all; shrinkage then handles the rest.
- **Rounding:** to two decimals, as the policy file stores them (Decimal strings; no floats anywhere, as in the rest of Nirnaya).
- **`q`:** the same estimator, with its own κ and minimum.

## 4. Constraints a proposal must meet

- **`p + q ≤ 1` for every entry,** which `MerchantPolicy` already enforces at load. A proposal that breaks it is rejected, not clipped.
- **Dominated entries are reported, not adjusted.** The lookup takes the highest `p` among present evidence sets, so finding more evidence can never lower `p`. A calibrated entry whose `p` falls below an entry with a subset of its evidence would simply never be chosen. The proposal lists such entries for a person to decide (merge, drop, or keep). No automatic smoothing across entries.
- **Unchanged structure.** Calibration changes values only: no new entries, no new evidence types, no new reasons. Changing the table's structure is a policy decision for a person.

## 5. Selection bias: what cannot be learned

Labels exist only for cases that were **challenged**. An entry with a low prior is rarely challenged, so it rarely gets labels: calibration can confirm or lower high priors faster than it can raise low ones. Those entries keep their prior and are reported as unlabelled. Exploration (deliberately challenging some low-`p` cases to learn) costs money and is out of scope. A merchant could choose it explicitly.

Referred cases that an approver challenged **do** produce labels, attributed the same way.

## 6. Output and review

- **The job:** `uv run python -m nirnaya.calibration propose --merchant <id>`, offline, reading the store. It writes:
  1. a candidate policy file `nirnaya/assets/policies/<merchant>/<next-version>.toml`, the current file with new `p` (and `q`) values and a header comment recording the run;
  2. a calibration report per entry: `n`, `w`, the observed rate, `p0`, `p_new`, insufficient-data and dominated flags, and the **decisions that would flip** if the candidate were active (the stored cases re-decided with the candidate table, so a person sees the effect before adopting it).
- **Adopting it is a person's step,** never automatic:
  - review the report;
  - `uv run python -m nirnaya.assets lock` to lock the new file;
  - pin it in `active.toml`;
  - commit with the report's table in the message.

  No env override (§16.1: a policy change alters decisions, so it goes through a committed pin). Old versions stay locked and loadable, so any past decision can be re-derived with the policy it used.
- **Tenant isolation:** calibration reads one merchant's cases only and writes only that merchant's policy. No pooling across merchants. That would be a separate, explicit decision.

## 7. Invariants

- **I2 (determinism).** The engine and verifier are untouched; calibration is an offline job producing a data file. The same store contents and κ always give the same proposal.
- **I1.** Calibration decides nothing at runtime and authorises nothing. It proposes numbers a person adopts.
- **I7.** It reads only statuses Nirnaya already maps from Airwallex. Realised fees (`F_cb`, `F_pa`, `F_rfi`) are not calibrated here; they await Airwallex pricing (architecture §17.2 #7 and #15).

## 8. Open questions for review

1. κ = 20 and n ≥ 10: are these the right starting points, or should κ scale with how confident the merchant is in its current priors?
2. Should `EXPIRED` after a challenge count as a loss (it costs the merchant the amount) rather than be excluded?
3. Should a proposal also be run through the evaluation report (42 synthetic cases) as a regression check, or are synthetic cases misleading once real outcomes exist?
4. How often to propose: on demand only, or a scheduled reminder once *n* new labels accrue?

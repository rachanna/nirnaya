# Failure and edge cases

An audit of commit `90b9a63`, made on 6 November 2026. Four reviews covered the write path, the case lifecycle, the deterministic core, and the model and demo surfaces. Each finding came with a scenario, and most with a reproduction against the offline fakes. No live Airwallex or model call was made.

## Status

**Fixed, each with a regression test:** FC-01 to FC-10, FC-14 to FC-17, FC-20 and FC-21. The original reproductions were run again against the fixes, and the 42-case evaluation, the note suite and the self-correction suite did not change.

**Open:** FC-11 to FC-13, FC-18, FC-19, FC-22 and FC-27 could be fixed next. FC-23 to FC-26, FC-28 and FC-29 are left as they are for now: they need real merchant data, are unlikely, or are cosmetic.

**Limits of the fixes:**

- FC-02: the console still doesn't show the refunded amount to the approver. A change to it voids the decision, so nobody approves on stale facts, but they aren't told the figure.
- FC-04: placeholders are a list, so a carrier's own wording for "no signature" can still pass until it's added.
- FC-07: a case held by a write in doubt stays with a person, who checks Airwallex. No command clears the doubt, so an approval is refused until the write shows as landed.
- FC-09: a real run refuses disputes a dry run took through, so they need `--fresh`. Nothing re-opens them.

The tables below describe each case as it was found.

**How to read this page**

- **Severity:** *Critical* means a wrong money action or a false statement could reach Airwallex. *High* means a stuck case, a missed deadline, a wrong ledger or a failure on camera. *Medium* means a safe failure with a wrong or confusing result.
- **Checked:** *Re-run* means the reproduction was run a second time, independently of the review that found it. *Code* means confirmed by reading the code. *Review* means reported with a reproduction by the review, and not yet independently checked.
- **Demo:** whether it can happen in the hackathon demo as staged. None of these affect the recorded 30 October run.
- **Trigger:** several findings need merchant data or Airwallex behaviour the kit cases don't have. That limits how likely they are, not how serious they would be.

## Critical

| ID | What goes wrong | Trigger | Checked | Demo |
|---|---|---|---|---|
| FC-01 | **A failed or lazy model causes an automatic accept.** If the first investigator call fails, or the model finishes early, code decides on what it has. With no carrier record the engine says accept, and the accept is written, while value of information still says one lookup would flip it. Across the 42 cases, 7 challenges and 4 referrals became accepts (3,141 USD). This breaks rule 1 in effect: the model's failure authorises a refund. | Rate limit, bad key, refusal, or the model stopping early with `NIRNAYA_AGENT=llm` | Re-run | **Yes:** live tab |
| FC-02 | **A refund posted after ingestion is ignored.** The refund is checked only once, at ingestion. Re-assessment, the gateway and the approval binding compare the amount alone. So an accept can go out on a fully refunded dispute, paying the customer twice, and a challenge can go out with a claim the verifier would now block. The console doesn't show the refund either. | A refund lands mid-flow or while a case waits for a person | Re-run | No |
| FC-03 | **Any refund counts as "refund issued".** A 0.01 refund establishes the fact, lifts the odds to 0.85 and turns an accept into a challenge. A partial refund also replaces the real defence, so 1 USD refunded on a 1,500 USD dispute tells Airwallex `CUSTOMER_REFUNDED`. Reversed, oversized and other-currency refunds pass too. | A partial refund on the merchant's order | Re-run | No |
| FC-04 | **Placeholder text counts as a signature.** `signed_by = "NOT SIGNED"` or `"N/A"` establishes the signature, and the response claims "Delivery signed for by: NOT SIGNED". | A carrier that fills the field with a placeholder | Re-run | No |
| FC-05 | **Any reply on a ticket counts as answering every email.** A reply before the second email, or `replied_at = "PENDING"`, clears the "unanswered customer" referral, so kit case 3 becomes an automatic accept. | One ticket holding several emails | Re-run | Only if a reply is added |
| FC-06 | **A small amendment overrides a person.** After an amount change, the note reader doesn't run again and the case re-decides without the quotes. A pending quote is dropped unseen; a person's approved challenge becomes an automatic accept; or a confirmed quote is sent with no live approval. | An amendment on a case with a note finding | Re-run | No |

## High

| ID | What goes wrong | Trigger | Checked | Demo |
|---|---|---|---|---|
| FC-07 | **A second write goes out while the first is unresolved.** With a challenge in `UNKNOWN`, a re-decision produces a new `request_id` and a second challenge is sent with no person involved. A person can also approve an accept on top of it. | A timeout plus an amendment or referral | Re-run | No |
| FC-08 | **A failed resend erases "may have landed".** `UNKNOWN` becomes `FAILED` and the warning to the person disappears, while Airwallex shows the dispute challenged. | A timeout, then a failing resend | Re-run | Unlikely |
| FC-09 | **Dry run and real runs collide on one database.** A dry run overwrites a real `UNKNOWN` write, so a later `--submit` sends nothing while Airwallex still waits. Rehearsing a dry run on staged disputes then running `--submit` also sends nothing, and exits successfully. | Re-running on the same database | Re-run | **Yes:** rehearsal |
| FC-10 | **After one guard failure, approval can never succeed.** The new approval binds only the guard's reason, so the engine's original reason looks new and every retry is void. The only way out is closing the case by hand. | One temporary read failure or a rejected write on an approved case | Re-run | **Yes:** approving case 3 |
| FC-11 | **Some referred cases can't be recovered.** A failure before any decision leaves a case no approval can fix, because it can't be sent back to be assessed. An amendment seen during submission refers a case that then fails every approval. | Three failed ticks at ingest | Re-run (first case), Review (second) | Possible |
| FC-12 | **A closed case swallows a later stage change.** After a person closes a case by hand, an escalation to chargeback raises no case and no alert, and the deadline passes. | Manual close, then an escalation | Re-run | Unlikely |
| FC-13 | **Nothing watches a case after a write.** Airwallex's returned status is stored without being checked. If a dispute is still `REQUIRES_RESPONSE` near its deadline, nothing alerts anyone. | Airwallex reopening a dispute (not observed), or a dry run | Re-run | Dry run only, harmless |
| FC-14 | **A quote can start mid-word.** "signed by recipient" passes as word for word from "unsigned by recipient" and goes out as a confirmed quote. | The model trims a quote | Re-run | Unlikely |
| FC-15 | **The web page hides the note behind a quote.** A person confirms a fragment without seeing the whole note. "No signature captured" can be shown as "signature captured". The Streamlit console shows the whole note. | The model trims a quote | Code | **Yes:** a judge may ask |
| FC-16 | **"The bank rejects" moves the clock for every case.** After that step, Demo B shows "Deadline buffer reached" under the headline "Cheaper to settle early". | Walking the tabs in order | Re-run | **Yes:** the natural order |
| FC-17 | **Streamlit shows a raw error with no model key.** With `NIRNAYA_AGENT=llm` and no key, nothing else on the page can be reached. | A missing or wrong `.env` | Code | Fresh machine |
| FC-18 | **A quoted signature on a lost parcel isn't a conflict.** Contradiction rules guard "delivered" but not "signed", so a person isn't shown the conflict when confirming the quote. | A crafted or mistaken note | Review | No |
| FC-19 | **A model or data crash loses its audit.** A tool error or an unusual value (`1E+6000`) fails the whole step, with model calls made but not stored. On the web, the case then sits with no error and no buttons. | A merchant system error, or an unusual gateway | Review | Unlikely |

## Medium

| ID | What goes wrong | Checked | Demo |
|---|---|---|---|
| FC-20 | Priors are shown as odds: "The odds of winning go from 60% to 75%". CLAUDE.md says priors are never presented as predictions. It's in the recorded case 1 story. | Code | **Yes:** recorded tab |
| FC-21 | `Retry-After` is not capped, so a value of 86,400 sleeps for a day. Commit `90b9a63` said capped. It also applies before a write, with no re-check afterwards. | Re-run | Unlikely |
| FC-22 | The verifier raises an error on an impossible date or a huge number instead of blocking the claim. It fails safe after three ticks. | Re-run (date) | No |
| FC-23 | Contradiction rules need exact text, so `lost` or `Returned_To_Sender` is not treated as a conflict. Harmless with the mock systems; serious with a real adapter. | Review | No |
| FC-24 | Name matching passes a different person: claim "J. Smith" against record "Not J. Smith" or "J. Smith-Jones". | Review | Unlikely |
| FC-25 | Only cited records are hash-checked, so changing an uncited record can hide counterevidence. It needs a write to the store. | Review | No |
| FC-26 | The PDF says "quoted word for word" but replaces curly quotes, dashes and non-Latin text with `?`. | Review | No |
| FC-27 | `--fresh` puts the ledger in one database and the next run opens another, splitting the record of writes. | Re-run | **Yes:** a `--fresh` run then a resume |
| FC-28 | The verdict badge still says "Fight it" after a person handles the case outside Nirnaya. | Review | If pressed |
| FC-29 | The web page says "Claude" when the provider is OpenAI. | Review | On an OpenAI key |

## Reviewed, no change needed

- **Partial refunds and the decision amount.** One review said the engine should decide on the net amount. An accept debits the full disputed amount whatever was refunded, so the gross amount is the correct stake. The wording in `docs/kit4-checklist.md` and architecture §4.3 ("netted") overstates this and should be corrected.
- **Deferred cases, FX and the budget referral.** A deferred case isn't re-checked when its premise changes; cross-currency re-decisions disagree; `COUNTEREVIDENCE_INCOMPLETE` can't be cleared by a person; a referral at the margin doesn't search for evidence. All fail safe, and the FX path isn't reachable today.
- **Model timeouts and token budget.** A hung call can take about three minutes, and a round can overrun the token budget. Both are bounded.
- **Low-severity items.** Duplicate claims, a stale urgent flag, repeated audit rows, a counter that doesn't reset, delimiter variants, `.env` encoding, and a missing idempotency key on sandbox staging.
- **Audit tail deletion.** Removing the newest audit rows passes `verify_chain`. This needs a stored head hash, which belongs with a production database.

## What held up

- The decision maths matched the formulas in CLAUDE.md on 20,000 randomised cases.
- Crash points around a write recover correctly, with no duplicate send.
- Every transition a real sequence can trigger is allowed, so no crash from an illegal state.
- Every store query is scoped by merchant, apart from the two lookups that are meant to span merchants.
- Malformed model output, invented keys and refusals all failed safe, apart from FC-01.
- Unknown stages, statuses, brands, null fields and bad timestamps are marked unreadable, never guessed.
- Commit `90b9a63`'s retries are correct, apart from FC-21.

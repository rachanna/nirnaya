# Demo script (presentation slot, about 5 minutes)

A run sheet for presenting Nirnaya live. Every number here comes from the repo; the claims are the ones the evidence supports (see "Say / don't say").

There is no video: this is for the live presentation. The run sheet uses the Streamlit console, which needs no network or model key. The web page's recorded tab is the proof from the real sandbox.

## Before you start

- `uv run python -m nirnaya.preflight --live` on the machine you present from. It checks the page, recording, results, the kit cases, the replays, `DRY_RUN`, the ports and the model key, and it never calls Airwallex. Everything should be OK; a warning means one part isn't available.
- `uv run streamlit run app/streamlit_app.py` with the default `NIRNAYA_AGENT=scripted` (no network, no tokens; the note reading is a labelled replay of a real Claude run). Open http://localhost:8501 and press **Start over**.
- A second terminal, scrolled clear, for `uv run python -m nirnaya.demo` (demo A).
- Browser zoom about 90% so a whole kit card fits on screen.
- `uv run python -m nirnaya.web`, open http://localhost:8000 and leave it on **Recorded live run**: the real sandbox run of 30 October, with K1 challenged twice, citing the signature, K2 accepted, K3 referred. It plays from a local file, so it needs no network. The console itself runs on the offline fake.
- **The live sandbox works again.** From 7 to 10 November it answered every challenge that had a file with HTTP 500. On 10 November challenges were confirmed again and the whole kit ran end to end. Still show the recorded tab rather than a live run: it needs no network, and a live run takes test payments first.

## Run sheet

| Time | Show | Say |
|---|---|---|
| 0:00 | Title / README top | "A finance-ops lead works a chargeback queue: fight or accept, on evidence and money. Nirnaya decides which disputes are worth fighting, finds evidence Airwallex cannot see, and refuses to submit any claim it cannot prove." |
| 0:20 | README "How it works" table | "Three parts. The model investigates and drafts; it authorises nothing. A deterministic engine decides on the merchant's economics. A deterministic verifier checks every claim against its source record. One guarded gateway is the only path to Airwallex." |
| 0:45 | Console → **Run the kit cases** | Read case 1's steps: "With Airwallex's data alone, a person would decide: accepting now refunds everything. Nirnaya checked the merchant's records and found three earlier orders from the same device, so it is worth fighting; then it looked up the signed delivery to make the fight stronger. Decided: fight it, worth about 970 USD more than accepting." Open **Details** only if asked for the numbers. |
| 1:15 | K2, K3 cards | "K2: 15 USD, fees exceed the amount, accept with LOW_VALUE_TRANSACTION. K3: the customer emailed support twice with no reply, so a person decides; Nirnaya never denies a complaint exists." Point at the Airwallex AI line: "Airwallex's own AI recommendation sits beside ours (simulated offline, labelled)." |
| 1:40 | **The bank rejects case 1** | "The issuer rejects; Airwallex escalates to chargeback. A new cycle re-decides on the new stage and challenges again, with the device and IP sent as customer_info." |
| 2:00 | 2-C card | "An unguarded agent claims 'signed for by J. Smith'. The carrier record has no signature field, so the verifier blocks it with the reason. Nothing unverified ships." |
| 2:15 | **Run with a broken first draft** → **Needs a person** → The AI fixed its own mistake | "So what does our own agent do when it's blocked? Here its first draft is broken on purpose with that same fabrication. The verifier blocks it, the agent gets the findings and drafts again: red is what was blocked and why, green is the redraft that passed. Because the first draft overreached, a person still approves." Click **Fight it** under this case, as case 3 is in the same queue with its own button: "This sends exactly the draft I reviewed, verified again." Point at the label: a recorded Claude redraft, replayed. |
| 2:45 | Terminal: `uv run python -m nirnaya.demo`, scroll to N1 | Demo A: "N1 starts as accept, margin −60. Value of information says a delivery scan would flip it, so the agent looks only for that, finds it, and the decision flips to challenge, +40, verified." |
| 3:10 | Console → **Read the driver's note** → **Needs a person** → Evidence in a driver's note | "Evidence only a free-text note holds. The carrier shows delivered with no signature field, so the engine accepts; the driver's note says a signature was captured. The model may only propose the exact words. A person sees the quote in its whole note and what confirming changes: accept to challenge, prior 0.60 to 0.80." Tick, **Confirm and challenge**. |
| 3:40 | Back to the card | "Challenge sent; the quote claim is VERIFIED word for word, and confirmed by a person." |
| 3:50 | **Results** tab, open **All numbers** for the 39 of 42 / README tables | "42 synthetic cases against fight-all, accept-all and the model alone. Honestly: a well-prompted Claude agrees on 39 of 42, close to us. Our edge is guarantees that don't depend on a prompt or a run: rules are tested code, every claim is checked, the same answer every time, and the model never controls a write." |
| 4:20 | README agentic and self-correction tables | "Where the model adds value: reading notes, Claude found all 6 note-only facts and fell for none of the 4 traps, identically across 3 runs; a keyword list sent 3 traps to a person. And fixing blocked drafts: with first drafts broken on purpose, it repaired 15 of 15 of each kind, and every overreach still went to a person. Unsafe outcomes: zero, by construction." |
| 4:50 | Close | "Fight what pays, prove what you claim, and put a person where judgement is needed." |

Demo B (an early pre-chargeback accept, promised in the idea entry) runs offline only, because the sandbox auto-accepts pre-chargebacks within a second. It is the web demo's live tab "Cheaper to settle early" (case F5: 1,000 USD fraud, nothing on file; Claude looks for login history, finds none, and Nirnaya settles now, about 40 USD better than fighting). The **Results** view shows every baseline on the 42 cases. Say plainly that a well-prompted model comes close on loss; the difference is guarantees.

## Say / don't say

| Say | Don't say |
|---|---|
| "Close to a well-prompted model on decisions; better on guarantees" | "Nirnaya decides better than an LLM" (informed Claude: 39/42, slightly lower expected loss) |
| "42/42 against our written expert policy: it shows the code follows its policy" | "42/42 accuracy" |
| "Priors are fixed and shown as priors" | "Predicted win rate" |
| "Run end to end on the Airwallex sandbox: real accept and challenges confirmed on 29 and 30 October" | "Live with merchants" or "in production" (sandbox only; the merchant systems are synthetic) |
| "Unsafe outcomes zero by construction" | "The model never makes mistakes" |
| "A small synthetic suite, labelled by us" | Any real-world accuracy claim |
| K1's flip comes from code's intake lookup; N1 shows the agent's search | "The agent found K1's evidence" |
| "With first drafts broken on purpose, it repaired 15 of 15" | "The model fixes its own mistakes" (the breakage was code's, and the findings name the field) |
| "Every claim is checked" | A number of claims (the model drafted 8 for K1 on 30 October and on 10 November, and 9 in the 7 November rehearsal; the decision and the 970 don't change) |
| "Challenges were confirmed on 29 and 30 October and 10 November; in between, the sandbox returned a 500 for three days, and Nirnaya recovered without a duplicate" | That the 500's cause is known (only Airwallex can say) |

## If something breaks

- **Console error or blank page:** press **Start over**, then rerun. Fallback: `uv run python -m nirnaya.demo` shows K1–K3, S5 and N1 in the terminal.
- **No network or no model key:** stay on the console and the recorded tab, which need neither. On the live tab a failed model sends the case to a person, never to an accept.
- **A case shows Try again:** something failed before a decision. Press it, or use the recorded tab.
- **"No cached reading" on the note button:** the cache is committed in `eval/results/agentic/`; check you're on `main`. Live fallback: `NIRNAYA_AGENT=llm` (one model call).
- **"No cached redraft" on the self-correction button:** the cache is in `eval/results/self_correction/`. Live fallback: `NIRNAYA_AGENT=llm` (one model call).
- **Questions about numbers:** `eval/results/report.md` has every table, the variance across runs and the sensitivity section.

## Likely questions

- **"What happens when Airwallex itself fails?"** It did, on 7 November: challenges started returning HTTP 500. Nirnaya treated each one as "may have landed", sent no duplicate, made no accept, re-checked the dispute and re-sent under the same `request_id`, kept Airwallex's trace id in the audit, and the runner exited with a failure. Direct tests on throwaway disputes found nothing in our request, file or stage that explains it, and it was reported to Airwallex. When the sandbox recovered on 10 November, the three held challenges were re-sent under their original `request_id` and all three were confirmed, with no duplicate.
- **"Why do the reply dates say March 2026?"** The merchant records and test cases are synthetic, with fixed dates, so every run gives the same answer.
- **"What if the fees or priors are wrong?"** They are merchant configuration, labelled assumptions. The report re-runs every decision with one assumption changed: q from 0.10 to 0.25 flips 14 of 40. A merchant sets them; `docs/calibration.md` describes learning priors from outcomes.
- **"Why not just let the LLM decide?"** It nearly can on these cases. But one rule it wasn't told (a subscription needs a second piece of evidence) was silently not applied; in Nirnaya that rule is code and tested. And its results vary by run; ours don't.
- **"Can a note inject instructions?"** It's shown to the model only as data. At most it becomes a proposed quote, which must be word for word in the note, confirmed by a person, and verified. Customer text is never shown to the model.
- **"If the agent can fix its draft, why does a person still approve?"** A draft that claimed something its records don't support is a sign the model overreached; the fix passing the verifier proves the new words are supported, not that the model is now right about the case. Format slips (a true value in the wrong form) go out without a person.
- **"What does a person do that code can't?"** Judge meaning. The verifier checks a quote's words, not what they mean, so confirmation is required, not optional.
- **"Is this on the real Airwallex API?"** Yes, on the sandbox. On 29 and 30 October the kit ran end to end with real writes: K1 challenged at RFI with evidence uploaded (on 30 October citing the signed delivery), then again at chargeback after the simulated rejection; K2 accepted; K3 referred. The first attempt found a real bug: Airwallex's file host blocked our upload, and the engine failed safe by referring rather than challenging without evidence. A run on 7 November confirmed the accept and the referral again; its challenges hit the 500 above. On 10 November the whole kit ran again, end to end, with both of K1's challenges confirmed.

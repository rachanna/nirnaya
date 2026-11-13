# Video script (walkthrough, under 5 minutes)

The submission video. Judges may see only this and the repo, so it has to stand on its own: the problem, how it works, proof on the real API, the guardrails, and honest results. Target 4:25; the hard limit is 5:00, including any title card. Claims follow `docs/demo-script.md` "Say / don't say".

## Set up before recording

- `uv run python -m nirnaya.preflight` passes.
- **Console:** `uv run streamlit run app/streamlit_app.py` (default scripted agent: no network, no tokens), http://localhost:8501, press **Start over**.
- **Web demo:** `uv run python -m nirnaya.web`, http://localhost:8000, on **Recorded live run**.
- **Terminal:** run `uv run python -m nirnaya.demo` once, so beat 8 is ready to scroll. Font about 18 pt.
- **Screen:** 1920×1080, browser zoom so a whole card fits, bookmarks bar hidden, notifications off (Windows Focus).
- **Nothing private on screen:** no `.env`, no terminal history with keys, no Airwallex web app (account details).

Record each beat as its own clip and stitch them (Clipchamp comes with Windows 11; OBS also works). Voice-over can be recorded live or added afterwards.

## Beats

### 1. The problem (0:00, 25 s)
**Show:** a title card ("Nirnaya: dispute decisions you can prove"), then the top of the README.
**Say:** "When a customer disputes a card payment, the merchant has days to choose: fight it or accept it. Fighting costs fees and time, and a weak case loses anyway. Nirnaya makes that choice for a merchant on Airwallex. It decides which disputes are worth fighting, finds evidence Airwallex can't see, and refuses to submit any claim it can't prove."

### 2. How it works (0:25, 30 s)
**Show:** the README "How it works" table.
**Say:** "There are three parts. An AI agent investigates and drafts the response, but it authorises nothing. A deterministic engine decides, using the merchant's own fees and margins. A deterministic verifier checks every claim against the record it came from. And one guarded gateway is the only path to Airwallex's API."

### 3. Real run on the Airwallex sandbox: case 1 (0:55, 55 s)
**Show:** web demo, **Recorded live run**, case 1. Scroll with the narration.
**Say:** "This is a real run on the Airwallex sandbox: real test payments, real disputes, real API calls, replayed from its recording. Case 1 is a 1,500 dollar fraud dispute. With only Airwallex's data, accepting looks right. Nirnaya checks the merchant's records and finds three earlier orders from the same device, so fighting pays. Then it fetches the signed delivery to make the case stronger: about 970 dollars better than accepting. Airwallex confirms the challenge. The bank rejects it, the dispute escalates, and Nirnaya decides again and challenges again, with the device and IP attached. Confirmed again."

### 4. Cases 2 and 3 (1:50, 20 s)
**Show:** the recorded tab, cases 2 and 3.
**Say:** "Case 2 is 15 dollars: the fees cost more than the dispute, so it accepts. Case 3: the customer emailed support twice and got no reply. That's a judgement call, so it goes to a person. Nirnaya never denies a complaint exists."

### 5. The verifier blocks a fabrication, and the agent fixes its draft (2:10, 30 s)
**Show:** console → **Run the kit cases** → the 2-C card; then **Run with a broken first draft** → **Needs a person** → the red and green drafts.
**Say:** "Now the guardrails. An unguarded agent claims the parcel was 'signed for by J. Smith'. The carrier record has no signature field, so the verifier blocks it and says why. Here our own agent's first draft is broken on purpose the same way. It's blocked, the agent gets the findings and redrafts, and the redraft passes. Because the first draft overreached, a person still approves it."

### 6. Evidence in a driver's note (2:40, 30 s)
**Show:** console → **Read the driver's note** → **Needs a person** → the quote in its note → tick → **Confirm and challenge** → the card shows VERIFIED.
**Say:** "Some evidence lives only in free text. The carrier record shows no signature, but the driver's note says one was captured. The model may only propose the exact words. A person sees the quote in its whole note, and what confirming changes: accept becomes challenge. Then the verifier checks the quote word for word."

### 7. The agent searches only where it matters (3:10, 20 s)
**Show:** terminal, the `nirnaya.demo` output scrolled to N1.
**Say:** "Where does the agent search? Only where evidence would change the decision. Case N1 starts as accept, 60 dollars short. The engine says a delivery scan would flip it, so the agent looks only for that, finds it, and the decision flips to challenge."

### 8. Results, honestly (3:30, 35 s)
**Show:** console **Results** tab, then the README agentic and self-correction tables.
**Say:** "We tested on 42 synthetic cases against fighting everything, accepting everything, and an LLM on its own. Honestly, a well-prompted Claude agrees with us on 39 of 42. The difference is guarantees: the rules are tested code, every claim is checked against its record, the same case gets the same answer every time, and the model never controls a write. Where the model adds value is reading notes: it found all 6 note-only facts and fell for none of the 4 traps."

### 9. Close (4:05, 20 s)
**Show:** the README top with the repo URL, then its "Setup" and "Run it" sections.
**Say:** "Fight what pays, prove what you claim, and put a person where judgement is needed. The code, the setup and every result are in the repository, and the offline demo runs without any keys."

**Ends about 4:25.**

## Optional beats (add at most one, or it runs past 4:45)

- **Recovery without a duplicate (15 s), after beat 4.** Show the README's sandbox paragraph. Say: "Between 7 and 10 November the sandbox returned errors on every challenge. Nirnaya treated each one as 'may have landed', sent no duplicate, and when the sandbox recovered it re-sent each under the same request ID. All three were confirmed."
- **Demo B, settling early (15 s), after beat 7.** This needs a model key and network. On the web demo's live tab, open "Cheaper to settle early". Say: "Here fighting would cost more than settling now: a 1,000 dollar fraud dispute with nothing on file. The agent looks for login history, finds none, and Nirnaya settles early, about 40 dollars better than fighting."

## Don't say

- "Nirnaya decides better than an LLM." Say that it's close on decisions and better on guarantees.
- "42/42 accuracy." The 42/42 is against our own written policy.
- "In production" or "live with merchants." It runs on the sandbox, and the merchant systems are synthetic.
- "The agent found case 1's evidence." Code's intake lookup found it; N1 shows the agent searching.
- A count of claims, or a cause for the sandbox errors.

## Before submitting

- Watch the whole video once: it must be under 5:00, with no key, account detail or notification on screen.
- Upload to YouTube as **Unlisted** (or a Drive link with "anyone with the link"), then open the link in a private window to check it plays.
- Make the GitHub repo public, then open the link in a private window.

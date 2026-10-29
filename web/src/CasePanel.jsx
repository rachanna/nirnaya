import { useCallback, useState } from "react";
import Calls from "./Calls.jsx";
import Feed from "./Feed.jsx";

const VERDICT = {
  CHALLENGE: { label: "Fight it", tone: "good" },
  ACCEPT: { label: "Accept it", tone: "warn" },
  REFER: { label: "A person decides", tone: "info" },
};

// One case: what it is and its controls, the story as a playable feed, and Claude's calls.
// `act` is null for the recorded run (it takes no actions).
export default function CasePanel({ number, eyebrow, story, recorded = false, model, act, busy }) {
  const [done, setDone] = useState(false);
  const onDone = useCallback((d) => setDone(d), []);
  const running = story.running;
  const ready = act && done && !running && !busy;
  const verdict = done && !running && story.verdict ? VERDICT[story.verdict] : null;

  return (
    <section className="panel" aria-label={`Case ${number}`}>
      <aside className="summary">
        <p className="eyebrow">{eyebrow ?? `Case ${number}`}</p>
        <h2>{story.headline}</h2>
        <p className="amount">{story.amount}</p>
        <p className="question">{story.question}</p>
        {verdict && <p className={`verdict tone-${verdict.tone}`}>{verdict.label}</p>}

        {act && (
          <div className="actions">
            {story.state === "NOT_RUN" && !running && (
              <button className="primary" disabled={busy} onClick={() => act("run")}>
                Run with Claude
              </button>
            )}
            {ready && story.can_reject && (
              <button onClick={() => act("reject")}>The bank rejects the evidence</button>
            )}
            {ready && story.needs_person && (
              <div className="decide">
                <p className="hint">You decide:</p>
                {story.proposed_quotes?.length > 0 && (
                  <>
                    <blockquote className="quote">
                      {story.proposed_quotes.map((q, i) => <p key={i}>“{q}”</p>)}
                    </blockquote>
                    <button className="primary" onClick={() => act("confirm")}>
                      Confirm the quote and fight it
                    </button>
                  </>
                )}
                <button onClick={() => act("accept")}>Accept (refund the customer)</button>
                <button onClick={() => act("fight")}>Fight it</button>
                <button className="quiet" onClick={() => act("manual")}>
                  Handle it outside Nirnaya
                </button>
              </div>
            )}
          </div>
        )}
        {story.error && <p className="error" role="alert">{story.error}</p>}
      </aside>

      <Feed
        key={story.label}
        messages={story.messages}
        autoplay={recorded}
        working={running ? "Nirnaya is working; Claude's calls appear on the right as they happen" : null}
        empty={<>Press <strong>Run with Claude</strong> to watch Nirnaya handle it, step by step.</>}
        onDone={onDone}
      />

      <Calls calls={story.calls ?? []} recorded={recorded} model={model} />
    </section>
  );
}

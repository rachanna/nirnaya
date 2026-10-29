import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const WHO = {
  airwallex: { name: "Airwallex", mark: "A" },
  nirnaya: { name: "Nirnaya", mark: "N" },
  ai: { name: "Claude", mark: "AI" },
  check: { name: "Check", mark: "✓" },
  person: { name: "A person", mark: "P" },
};

const VERDICT = {
  CHALLENGE: { label: "Fight it", tone: "good" },
  ACCEPT: { label: "Accept it", tone: "warn" },
  REFER: { label: "A person decides", tone: "info" },
};

const STEP_MS = 1500; // one message every 1.5 s at 1x
const TYPING_MS = 650; // the "…" before each message

function Message({ m }) {
  const who = WHO[m.who] ?? { name: m.who, mark: "?" };
  return (
    <li className={`msg tone-${m.tone} who-${m.who}`}>
      <span className="avatar" aria-hidden="true">{who.mark}</span>
      <div className="bubble">
        <div className="meta">
          <span className="who">{who.name}</span>
          <span className="title">{m.title}</span>
        </div>
        <p>{m.text}</p>
      </div>
    </li>
  );
}

function Typing({ who }) {
  const w = WHO[who] ?? WHO.nirnaya;
  return (
    <li className={`msg typing who-${who}`} aria-label={`${w.name} is working`}>
      <span className="avatar" aria-hidden="true">{w.mark}</span>
      <div className="bubble dots"><span /><span /><span /></div>
    </li>
  );
}

export default function CasePanel({ number, initial, onChange }) {
  const [story, setStory] = useState(initial);
  const [visible, setVisible] = useState(initial.messages.length);
  const [playing, setPlaying] = useState(false);
  const [typing, setTyping] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const feedEnd = useRef(null);

  const messages = story.messages;
  const done = messages.length > 0 && visible >= messages.length;
  const ran = story.state !== "NOT_RUN";

  // Playback: a typing pause, then the next message; stop at the end.
  useEffect(() => {
    if (!playing) return undefined;
    if (visible >= messages.length) {
      setPlaying(false);
      return undefined;
    }
    const wait = setTimeout(() => setTyping(true), (STEP_MS - TYPING_MS) / speed);
    const show = setTimeout(() => {
      setTyping(false);
      setVisible((v) => v + 1);
    }, STEP_MS / speed);
    return () => {
      clearTimeout(wait);
      clearTimeout(show);
    };
  }, [playing, visible, messages.length, speed]);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [visible, typing]);

  async function act(call, { fromStart = false } = {}) {
    setBusy(true);
    setError(null);
    try {
      const next = await call();
      setStory(next);
      onChange?.(next);
      if (fromStart) setVisible(0);
      setPlaying(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const verdict = done && story.verdict ? VERDICT[story.verdict] : null;

  return (
    <section className="panel" aria-label={`Case ${number}`}>
      <aside className="summary">
        <p className="eyebrow">Case {number}</p>
        <h2>{story.headline}</h2>
        <p className="amount">{story.amount}</p>
        <p className="question">{story.question}</p>
        {verdict && <p className={`verdict tone-${verdict.tone}`}>{verdict.label}</p>}

        <div className="actions">
          {!ran && (
            <button className="primary" disabled={busy}
              onClick={() => act(() => api.run(story.label), { fromStart: true })}>
              Run this case
            </button>
          )}
          {done && story.can_reject && (
            <button disabled={busy} onClick={() => act(() => api.reject(story.label))}>
              The bank rejects the evidence
            </button>
          )}
          {done && story.needs_person && (
            <div className="decide">
              <p className="hint">You decide:</p>
              <button disabled={busy} onClick={() => act(() => api.decide(story.label, "accept"))}>
                Accept (refund the customer)
              </button>
              <button disabled={busy} onClick={() => act(() => api.decide(story.label, "fight"))}>
                Fight it
              </button>
              <button className="quiet" disabled={busy}
                onClick={() => act(() => api.decide(story.label, "manual"))}>
                Handle it outside Nirnaya
              </button>
            </div>
          )}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </aside>

      <div className="feed-wrap">
        <div className="controls" role="toolbar" aria-label="Playback">
          <button aria-label={playing ? "Pause" : "Play"} disabled={!ran || done}
            onClick={() => setPlaying((p) => !p)}>
            {playing ? "❚❚ Pause" : "▶ Play"}
          </button>
          <button aria-label="Step" disabled={!ran || done}
            onClick={() => { setPlaying(false); setTyping(false); setVisible((v) => v + 1); }}>
            ⏭ Step
          </button>
          <button aria-label="Replay" disabled={!ran}
            onClick={() => { setVisible(0); setPlaying(true); }}>
            ↺ Replay
          </button>
          <div className="speed" role="group" aria-label="Speed">
            {[1, 2, 4].map((s) => (
              <button key={s} className={speed === s ? "on" : ""} onClick={() => setSpeed(s)}>
                {s}×
              </button>
            ))}
          </div>
          <span className="progress" aria-live="polite">
            {ran ? `Step ${Math.min(visible, messages.length)} of ${messages.length}` : "Not run yet"}
          </span>
        </div>

        <ol className="feed">
          {!ran && (
            <li className="empty">
              Press <strong>Run this case</strong> to watch Nirnaya handle it, step by step.
            </li>
          )}
          {messages.slice(0, visible).map((m, i) => <Message key={i} m={m} />)}
          {typing && visible < messages.length && <Typing who={messages[visible].who} />}
          <li ref={feedEnd} className="end" aria-hidden="true" />
        </ol>
      </div>
    </section>
  );
}

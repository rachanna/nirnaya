import { useEffect, useRef, useState } from "react";

const WHO = {
  airwallex: { name: "Airwallex", mark: "A" },
  nirnaya: { name: "Nirnaya", mark: "N" },
  ai: { name: "Claude", mark: "AI" },
  check: { name: "Check", mark: "✓" },
  person: { name: "A person", mark: "P" },
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

function Typing({ who, label }) {
  const w = WHO[who] ?? WHO.nirnaya;
  return (
    <li className={`msg typing who-${who}`} aria-label={label ?? `${w.name} is working`}>
      <span className="avatar" aria-hidden="true">{w.mark}</span>
      <div className="bubble dots"><span /><span /><span /></div>
      {label && <span className="typing-label">{label}</span>}
    </li>
  );
}

// Plays a case's messages one at a time. New messages (after an action) play on from where
// the feed is; `working` shows Nirnaya at work while the server runs the case.
export default function Feed({ messages, autoplay = false, working = null, empty, onDone }) {
  const [visible, setVisible] = useState(autoplay ? 0 : messages.length);
  const [playing, setPlaying] = useState(autoplay && messages.length > 0);
  const [typing, setTyping] = useState(false);
  const [speed, setSpeed] = useState(1);
  const seen = useRef(messages.length);
  const feedEnd = useRef(null);

  const done = visible >= messages.length;
  const has = messages.length > 0;

  useEffect(() => {
    if (messages.length > seen.current) setPlaying(true);
    if (messages.length < seen.current) setVisible(0);
    seen.current = messages.length;
  }, [messages.length]);

  useEffect(() => onDone?.(done), [done, onDone]);

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
  }, [visible, typing, working]);

  return (
    <div className="feed-wrap">
      <div className="controls" role="toolbar" aria-label="Playback">
        <button aria-label={playing ? "Pause" : "Play"} disabled={!has || done}
          onClick={() => setPlaying((p) => !p)}>
          {playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <button aria-label="Step" disabled={!has || done}
          onClick={() => { setPlaying(false); setTyping(false); setVisible((v) => v + 1); }}>
          ⏭ Step
        </button>
        <button aria-label="Replay" disabled={!has}
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
          {has ? `Step ${Math.min(visible, messages.length)} of ${messages.length}` : working ? "Working…" : "Not run yet"}
        </span>
      </div>

      <ol className="feed">
        {!has && !working && <li className="empty">{empty}</li>}
        {messages.slice(0, visible).map((m, i) => <Message key={i} m={m} />)}
        {typing && visible < messages.length && <Typing who={messages[visible].who} />}
        {working && done && <Typing who="nirnaya" label={working} />}
        <li ref={feedEnd} className="end" aria-hidden="true" />
      </ol>
    </div>
  );
}

import { useEffect, useState } from "react";
import { api } from "./api.js";
import CasePanel from "./CasePanel.jsx";

const DOT = { CHALLENGE: "good", ACCEPT: "warn", REFER: "info" };
const POLL_MS = 700;

const KIT_SIZE = 3; // after the kit's three cases come the two beyond it

function CaseTabs({ cases, active, setActive }) {
  return (
    <nav className={`tabs n${cases.length}`} role="tablist" aria-label="Cases">
      {cases.map((c, i) => (
        <button key={c.label} role="tab" aria-selected={active === c.label}
          className={active === c.label ? "tab on" : "tab"} onClick={() => setActive(c.label)}>
          <span className={`dot ${c.running ? "busy" : c.verdict ? DOT[c.verdict] : "none"}`}
            aria-hidden="true" />
          <span className="tab-n">{i < KIT_SIZE ? `Case ${i + 1}` : "Beyond the kit"}</span>
          <span className="tab-h">{c.headline}</span>
          <span className="tab-a">{c.amount}</span>
        </button>
      ))}
    </nav>
  );
}

function Recorded({ info }) {
  const [data, setData] = useState(null);
  const [active, setActive] = useState("K1");
  const [error, setError] = useState(null);
  useEffect(() => { api.recorded().then(setData).catch((e) => setError(e.message)); }, []);
  if (error) return <p className="error" role="alert">{error}</p>;
  if (!data) return null;
  const i = data.cases.findIndex((c) => c.label === active);
  return (
    <>
      <p className="mode-note">
        A replay of the run on <strong>{info.recorded_on}</strong>: {data.models.join(", ")} as the
        agent, against the <strong>{data.airwallex}</strong>. Every message below happened. It
        predates the supporting lookups, so case 1 cites the prior orders and not yet the signed
        delivery; the live tab shows the current behaviour.
      </p>
      <CaseTabs cases={data.cases} active={active} setActive={setActive} />
      <CasePanel key={`rec-${active}`} number={i + 1} story={data.cases[i]} recorded
        model={data.models.join(", ")} />
    </>
  );
}

function Live({ info, round }) {
  const [cases, setCases] = useState(null);
  const [active, setActive] = useState("K1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const running = cases?.some((c) => c.running);

  useEffect(() => {
    api.cases().then(setCases).catch((e) => setError(e.message));
  }, [round]);

  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => api.cases().then(setCases).catch(() => {}), POLL_MS);
    return () => clearInterval(t);
  }, [running]);

  if (!info.available) {
    return (
      <p className="error" role="alert">
        The live tab needs a model: {info.why_not} Set it in .env and restart the server.
      </p>
    );
  }

  async function act(label, what) {
    setBusy(true);
    setError(null);
    try {
      const call = { run: api.run, reject: api.reject }[what];
      const next = call ? await call(label) : await api.decide(label, what);
      setCases((all) => all.map((c) => (c.label === next.label ? next : c)));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !cases) return <p className="error" role="alert">{error}</p>;
  if (!cases) return null;
  const i = cases.findIndex((c) => c.label === active);
  return (
    <>
      <p className="mode-note">
        Runs now, with real calls to <strong>{info.model}</strong>. Airwallex is{" "}
        {info.airwallex} and the merchant records are synthetic, so nothing real is written. In the
        kit's three cases Claude writes the response; the two cases beyond the kit show it reading
        a note, and fixing a draft the checks blocked.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <CaseTabs cases={cases} active={active} setActive={setActive} />
      <CasePanel key={`live-${round}-${active}`} number={i + 1} story={cases[i]} model={info.model}
        eyebrow={i < KIT_SIZE ? `Case ${i + 1}` : "Beyond the kit"}
        busy={busy || running} act={(what) => act(active, what)} />
    </>
  );
}

export default function App() {
  const [info, setInfo] = useState(null);
  const [mode, setMode] = useState("recorded");
  const [round, setRound] = useState(0); // bumps on "Start over"
  const [error, setError] = useState(null);

  useEffect(() => { api.info().then(setInfo).catch((e) => setError(e.message)); }, []);

  async function startOver() {
    try {
      await api.reset();
      setRound((r) => r + 1);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Nirnaya</h1>
          <p className="tag">Card disputes, decided on evidence and money.</p>
        </div>
        {mode === "live" && info?.live.available && (
          <button className="quiet" onClick={startOver}>Start over</button>
        )}
      </header>

      <ol className="how">
        <li><strong>1 · Read</strong> Airwallex reports a dispute: how much, why, and the deadline.</li>
        <li><strong>2 · Check</strong> Nirnaya looks only for the merchant records that could change the answer.</li>
        <li><strong>3 · Decide and prove</strong> Rule-based code fights, accepts or asks a person. Claude writes the response, and code checks every claim before it is sent.</li>
      </ol>

      {error && <p className="error" role="alert">Can't reach the Nirnaya API: {error}</p>}

      {info && (
        <>
          <nav className="modes" role="tablist" aria-label="Demo">
            <button role="tab" aria-selected={mode === "recorded"}
              className={mode === "recorded" ? "mode on" : "mode"} onClick={() => setMode("recorded")}
              disabled={!info.recorded}>
              <span className="mode-t">Recorded live run</span>
              <span className="mode-s">
                {info.recorded ? `${info.recorded.recorded_on} · Claude on the Airwallex sandbox` : "not available"}
              </span>
            </button>
            <button role="tab" aria-selected={mode === "live"}
              className={mode === "live" ? "mode on" : "mode"} onClick={() => setMode("live")}>
              <span className="mode-t">Live with Claude</span>
              <span className="mode-s">
                {info.live.available ? `Real model calls now · ${info.live.model}` : "needs a model key"}
              </span>
            </button>
          </nav>
          {mode === "recorded" && info.recorded && <Recorded info={info.recorded} />}
          {mode === "live" && <Live info={info.live} round={round} />}
        </>
      )}

      <footer className="foot">
        Merchant records are synthetic. Every decision, check and write is made by rule-based
        code; Claude only gathers evidence and drafts.
      </footer>
    </div>
  );
}

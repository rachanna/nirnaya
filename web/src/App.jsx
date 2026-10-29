import { useEffect, useState } from "react";
import { api } from "./api.js";
import CasePanel from "./CasePanel.jsx";

const DOT = { CHALLENGE: "good", ACCEPT: "warn", REFER: "info" };

export default function App() {
  const [info, setInfo] = useState(null);
  const [cases, setCases] = useState(null);
  const [active, setActive] = useState("K1");
  const [round, setRound] = useState(0); // bumps on "Start over" to remount the panels
  const [error, setError] = useState(null);

  useEffect(() => {
    api.info().then(setInfo).catch(() => {});
    api.cases().then(setCases).catch((e) => setError(e.message));
  }, [round]);

  async function startOver() {
    await api.reset();
    setCases(null);
    setRound((r) => r + 1);
  }

  function changed(next) {
    setCases((all) => all.map((c) => (c.label === next.label ? next : c)));
  }

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Nirnaya</h1>
          <p className="tag">Card disputes, decided on evidence and money.</p>
        </div>
        <div className="top-right">
          {info && (
            <span className="pill" title="Decisions, checks and every write are rule-based code">
              AI agent: {info.agent}
            </span>
          )}
          {info && <span className="pill quiet">Airwallex: {info.airwallex}</span>}
          <button className="quiet" onClick={startOver}>Start over</button>
        </div>
      </header>

      <ol className="how">
        <li><strong>1 · Read</strong> Airwallex reports a dispute: how much, why, and the deadline.</li>
        <li><strong>2 · Check</strong> Nirnaya looks only for the merchant records that could change the answer.</li>
        <li><strong>3 · Decide and prove</strong> It fights, accepts or asks a person. Every claim it sends is checked first.</li>
      </ol>

      {error && <p className="error" role="alert">Can't reach the Nirnaya API: {error}</p>}

      {cases && (
        <>
          <nav className="tabs" role="tablist" aria-label="Cases">
            {cases.map((c, i) => (
              <button key={c.label} role="tab" aria-selected={active === c.label}
                className={active === c.label ? "tab on" : "tab"}
                onClick={() => setActive(c.label)}>
                <span className={`dot ${c.verdict ? DOT[c.verdict] : "none"}`} aria-hidden="true" />
                <span className="tab-n">Case {i + 1}</span>
                <span className="tab-h">{c.headline}</span>
                <span className="tab-a">{c.amount}</span>
              </button>
            ))}
          </nav>
          {cases.map((c, i) => (
            <div key={`${round}-${c.label}`} role="tabpanel" hidden={active !== c.label}>
              <CasePanel number={i + 1} initial={c} onChange={changed} />
            </div>
          ))}
        </>
      )}

      <footer className="foot">
        Merchant records are synthetic. Every decision, check and write is made by rule-based
        code; the AI only gathers evidence and drafts.
      </footer>
    </div>
  );
}

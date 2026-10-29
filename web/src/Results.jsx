import { useEffect, useState } from "react";
import { api } from "./api.js";

// The evaluation report (nirnaya/console/results.py): every approach on the same synthetic
// cases. Numbers are the report's own; nothing is recomputed here.

const usd = (s) => Number(s).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function LossChart({ policies }) {
  const rows = [...policies].sort((a, b) => Number(a.expected_loss) - Number(b.expected_loss));
  const max = Math.max(...rows.map((p) => Number(p.expected_loss)));
  return (
    <figure className="chart">
      <figcaption>
        <strong>Expected net loss on the same disputes</strong> · USD, lower is better
      </figcaption>
      <ul className="lbars">
        {rows.map((p) => (
          <li key={p.key} className={p.ours ? "lbar-row ours" : "lbar-row"}
            title={`${p.name}: ${usd(p.expected_loss)} USD`}>
            <span className="lbar-name">{p.name}</span>
            <span className="lbar-track">
              <span className="lbar" style={{ width: `calc((100% - 84px) * ${Number(p.expected_loss) / max})` }} />
              <span className="lbar-v">{usd(p.expected_loss)}</span>
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

const ROWS = [
  ["Expected net loss (USD)", (p) => usd(p.expected_loss)],
  ["vs Nirnaya (USD)", (p) => (p.ours ? "—" : `${Number(p.vs_nirnaya) > 0 ? "+" : ""}${usd(p.vs_nirnaya)}`)],
  ["Same decision as the expert", (p, n) => `${p.agreement}/${n}`],
  ["Claims sent that a record doesn't support", (p) => p.unsupported],
  ["Fights sent with no evidence", (p) => p.no_evidence],
  ["Merchant-policy breaks", (p) => p.violations],
  ["Decided automatically / sent to a person", (p) => `${p.automated} / ${p.referred}`],
];

export default function Results() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.results().then(setData).catch((e) => setError(e.message)); }, []);
  if (error) return <p className="error" role="alert">{error}</p>;
  if (!data) return null;
  const { cases, policies, caveat } = data;
  return (
    <section className="results" aria-label="Results">
      <p className="mode-note">
        Every approach decides the same <strong>{cases}</strong> synthetic disputes, with the same
        fees and odds. A well-prompted model comes close on these cases; what Nirnaya adds is
        guarantees: every claim is checked against the record it cites, the merchant's rules are
        tested code rather than a prompt, and the same case gets the same answer every run.
      </p>
      <LossChart policies={policies} />
      <div className="table-wrap">
        <table className="metrics">
          <thead>
            <tr>
              <th scope="col">On {cases} disputes</th>
              {policies.map((p) => (
                <th key={p.key} scope="col" className={p.ours ? "ours" : undefined}>{p.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([label, get]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                {policies.map((p) => (
                  <td key={p.key} className={p.ours ? "ours" : undefined}>{get(p, cases)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="small-print">
        {policies.filter((p) => p.model).map((p) => (
          <li key={p.key}>{p.name}: {p.model}.</li>
        ))}
        <li>{caveat}</li>
      </ul>
    </section>
  );
}

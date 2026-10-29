// Claude's model calls for one case: live as they happen, or the stored summary of a recorded run.

const STEP = {
  investigate: ["Investigate", "Choose which merchant records to look up."],
  draft: ["Draft", "Write the response. Every claim must cite one field of one record."],
  redraft: ["Redraft", "Fix the claims the checks blocked, from the checks' findings."],
  read: ["Read a note", "Propose exact quotes from a support note."],
};

const n = (x) => (x ?? 0).toLocaleString("en");

function short(tc) {
  const args = tc.arguments ?? {};
  if (typeof args.key === "string") return `${tc.name} · ${args.key}`;
  const list = Object.values(args).find(Array.isArray);
  return list ? `${tc.name} · ${list.length} item${list.length === 1 ? "" : "s"}` : tc.name;
}

function Call({ c, recorded }) {
  const [name, purpose] = STEP[c.step] ?? [c.step, ""];
  const running = c.status === "running";
  return (
    <li className={`call ${c.status}`}>
      <div className="call-head">
        <span className="call-n">#{c.n}</span>
        <span className="call-step">{name}</span>
        {c.turn > 1 && <span className="call-turn">turn {c.turn}</span>}
        {c.cycle > 1 && <span className="call-turn">round {c.cycle}</span>}
        <span className="call-stats">
          {running ? "waiting for Claude…" : `${n(c.input_tokens)} in · ${n(c.output_tokens)} out`}
          {c.ms != null && !running && ` · ${(c.ms / 1000).toFixed(1)} s`}
        </span>
      </div>
      <p className="call-purpose">{purpose}</p>
      {c.prompts?.length > 0 && <p className="call-line">Prompts: {c.prompts.join(", ")}</p>}
      {c.tools?.length > 0 && <p className="call-line">Tools offered: {c.tools.join(", ")}</p>}
      {running && <div className="bar" aria-hidden="true"><span /></div>}
      {c.status === "failed" && <p className="call-error">The call failed: {c.error}. Nirnaya fails safe.</p>}
      {c.summary && <p className="call-answer">{c.summary}</p>}
      {c.tool_calls?.length > 0 && !recorded && (
        <ul className="call-tools">
          {c.tool_calls.map((tc, i) => <li key={i}>Claude called <code>{short(tc)}</code></li>)}
        </ul>
      )}
      {c.reply && <p className="call-answer">“{c.reply}”</p>}
      {(c.sent?.length > 0 || c.tool_calls?.length > 0) && !recorded && (
        <details>
          <summary>Exactly what was sent and returned</summary>
          {c.sent?.map((s, i) => (
            <div key={i}>
              <p className="pre-label">Sent ({s.role === "tool" ? "a tool's answer" : "case data"})</p>
              <pre>{s.text}</pre>
            </div>
          ))}
          {c.tool_calls?.map((tc, i) => (
            <div key={`t${i}`}>
              <p className="pre-label">Returned: {tc.name}</p>
              <pre>{JSON.stringify(tc.arguments, null, 1)}</pre>
            </div>
          ))}
        </details>
      )}
    </li>
  );
}

export default function Calls({ calls, recorded = false, model }) {
  const tokens = calls.reduce((s, c) => s + (c.input_tokens ?? 0) + (c.output_tokens ?? 0), 0);
  return (
    <aside className="calls" aria-label="Claude's calls">
      <div className="calls-head">
        <h3>Claude's calls</h3>
        <span className="calls-total">
          {calls.length} call{calls.length === 1 ? "" : "s"} · {n(tokens)} tokens
        </span>
      </div>
      <p className="calls-note">
        {recorded
          ? `From the recorded run (${model}). The store keeps a summary of each call, not its text.`
          : `Live calls to ${model}, as they happen. Code checks everything Claude returns; Claude decides nothing.`}
      </p>
      <ol className="call-list">
        {calls.length === 0 && (
          <li className="calls-empty">
            {recorded
              ? "No model call in this case: the rules and records settled it."
              : "No calls yet. Claude is called only for steps that need it."}
          </li>
        )}
        {calls.map((c) => <Call key={`${c.cycle ?? ""}-${c.n}`} c={c} recorded={recorded} />)}
      </ol>
    </aside>
  );
}

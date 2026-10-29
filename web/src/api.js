// The Python API (nirnaya/web/api.py). Every call returns JSON or throws a readable error.

async function call(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body;
}

export const api = {
  info: () => call("/info"),
  recorded: () => call("/recorded"),
  cases: () => call("/cases"),
  run: (label) => call(`/cases/${label}/run`, { method: "POST" }),
  reject: (label) => call(`/cases/${label}/reject`, { method: "POST" }),
  decide: (label, choice) =>
    call(`/cases/${label}/decide`, { method: "POST", body: JSON.stringify({ choice }) }),
  reset: () => call("/reset", { method: "POST" }),
};

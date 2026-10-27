"""A case's story from the audit log, for the dev and sandbox runners."""

from nirnaya.store import Store


def case_trace(store: Store, merchant_id: str, case_id: str) -> list[str]:
    lines = []
    for e in store.audit_events(merchant_id, case_id):
        b = e["body"]
        if e["event"] == "STATE":
            keys = ("action", "margin", "reasons", "outcome", "blocked", "failure", "detail")
            extra = {k: b[k] for k in keys if b.get(k)}
            lines.append(f"   {b['from']:>16} -> {b['to']:<16} {extra or ''}")
        elif e["event"] == "TOOL_CALL":
            lines.append(f"   {'lookup':>16}    {b['tool']}({b['key']}) -> {b['records']}")
        elif e["event"] == "TOOL_REFUSED":
            lines.append(f"   {'refused':>16}    {b['tool']}({b['key']}): {b['why']}")
        elif e["event"] == "AGENT_RUN":
            lines.append(
                f"   {'agent':>16}    {b['kind']} {b['provider']}/{b['model']} "
                f"stop={b['stop']} turns={b['turns']} tokens={b['tokens']}"
            )
        elif e["event"] in {"NEW_CYCLE", "SUBMITTED_WITH_NOTES", "REFERRAL_ESCALATED"}:
            lines.append(f"   {e['event'].lower():>16}    {b}")
    for w in store.writes(merchant_id, case_id):
        mode = " (dry run: not sent)" if w.dry_run else ""
        lines.append(f"   write: {w.action} cycle {w.cycle} {w.status}{mode}")
    return lines

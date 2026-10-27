"""Kit 4 on the real Airwallex sandbox: stage the three cases, decide, act, handle the rejection.

    uv run python -m nirnaya.sandbox_demo              # stage + dry run (nothing is sent)
    uv run python -m nirnaya.sandbox_demo --offline    # the same against the in-memory fake
    uv run python -m nirnaya.sandbox_demo --submit     # real accept/challenge (DRY_RUN=false)
    uv run python -m nirnaya.sandbox_demo --fresh      # stage new disputes instead of reusing
    uv run python -m nirnaya.sandbox_demo --checkout   # pay in the browser (hosted checkout)

Staging creates and confirms one payment intent per case with the test card and raises a
dispute on it (kit 4 guide): K1 1,500 USD at RFI (Visa 10.4), K2 15 USD at CHARGEBACK (13.1),
K3 120 USD at CHARGEBACK (13.6). Each intent carries the synthetic order's merchant_order_id,
so the SYNTHETIC merchant systems join to the real disputes.

Safety:
- the orchestrator only ever sees the disputes this runner staged (ScopedAirwallex);
- DRY_RUN=false is refused without --submit, and --submit asks for confirmation;
- the issuer's rejection (escalate K1) is simulated only after K1 was really challenged.
"""

import argparse
import html
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.integrations.airwallex.client import AirwallexClient
from nirnaya.integrations.airwallex.http import AirwallexHttp, Credentials
from nirnaya.integrations.airwallex.scoped import ScopedAirwallex
from nirnaya.integrations.airwallex.simulation import (
    PAID,
    AirwallexSimulator,
    SandboxSimulator,
)
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.integrations.mocks.simulator_fake import FakeSimulator
from nirnaya.models.enums import DisputeStatus, Stage
from nirnaya.orchestrator.ports import AirwallexError
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect, verify_chain
from nirnaya.trace import case_trace

KIT_CASES = ("K1", "K2", "K3")
M = DEMO_POLICY.merchant_id
DATA_DIR = Path(".nirnaya")
RESPONSE_WINDOW = timedelta(days=15)


@dataclass
class RunState:
    """Staged ids, kept between runs so a re-run reuses the same sandbox disputes."""

    run: str
    cases: dict[str, dict[str, str]] = field(default_factory=dict)
    rejected: bool = False

    @classmethod
    def load(cls, path: Path | None, fresh: bool, now: datetime) -> "RunState":
        if path is not None and path.is_file() and not fresh:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(data["run"], data.get("cases", {}), data.get("rejected", False))
        return cls(run=now.strftime("%Y%m%dT%H%M%S"))

    def save(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"run": self.run, "cases": self.cases, "rejected": self.rejected}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# The hosted checkout's client_secret lasts 60 minutes; an older unpaid intent is replaced.
SECRET_LIFETIME = timedelta(minutes=55)
CHECKOUT_SDK = "https://static.airwallex.com/components/sdk/v1/index.js"


def stage(
    sim: SandboxSimulator,
    state: RunState,
    cases: dict[str, EvalCase],
    now: datetime,
    out: Callable[[str], None],
    *,
    checkout: bool,
    save: Callable[[], None],
) -> list[str]:
    """Create, pay and dispute each kit case, saving after every step so nothing is created
    twice. Returns the cases still waiting for a payment on the hosted checkout page."""
    waiting = []
    for cid in KIT_CASES:
        d = cases[cid].dispute
        st = state.cases.setdefault(cid, {})
        if "dispute" in st:
            out(f"   {cid}: reusing dispute {st['dispute']}")
            continue
        created = st.get("created")
        stale = created is not None and now - datetime.fromisoformat(created) > SECRET_LIFETIME
        if "intent" in st and stale and sim.intent_status(M, st["intent"]) != PAID:
            out(f"   {cid}: unpaid intent {st['intent']} is older than its checkout secret")
            # Keep the attempt count: the next intent needs a new request_id, not the first one.
            attempt = st.get("attempt", "1")
            st.clear()
            st["attempt"] = attempt
        if "intent" not in st:
            attempt = int(st.get("attempt", "0")) + 1
            intent = sim.create_intent(
                M,
                key=f"{state.run}:{cid}:{attempt}",
                amount=d.amount,
                merchant_order_id=d.merchant_order_id,
            )
            st.update(
                intent=intent.payment_intent_id,
                secret=intent.client_secret or "",
                created=now.isoformat(),
                attempt=str(attempt),
            )
            save()
            out(f"   {cid}: payment intent {intent.payment_intent_id} ({d.amount})")
        status = sim.intent_status(M, st["intent"])
        if status != PAID and not checkout:
            status = sim.confirm_with_test_card(
                M, key=f"{state.run}:{cid}:{st['attempt']}", payment_intent_id=st["intent"]
            ).status
        if status != PAID:
            waiting.append(cid)
            out(f"   {cid}: waiting for payment ({status})")
            continue
        st["dispute"] = sim.create_dispute(
            M,
            payment_intent_id=st["intent"],
            reason_code=d.reason.original_code or "",
            stage=d.stage,
            due_at=now + RESPONSE_WINDOW,
        )
        st.pop("secret", None)  # paid: the checkout secret is no longer needed
        save()
        out(f"   {cid}: paid; dispute {st['dispute']} at {d.stage}, Visa {d.reason.original_code}")
    return waiting


def checkout_page(state: RunState, cases: dict[str, EvalCase], waiting: list[str]) -> str:
    """A local page with one Pay button per waiting case, using Airwallex's hosted checkout
    (Quickstart: your first online payment, checked 2026-10-27). Card data never touches
    Nirnaya: the shopper types the test card on Airwallex's page."""
    intents = {
        cid: {
            "intent_id": state.cases[cid]["intent"],
            "client_secret": state.cases[cid]["secret"],
            "currency": cases[cid].dispute.amount.currency,
        }
        for cid in waiting
    }
    buttons = "\n".join(
        f'<p><button data-case="{html.escape(cid)}">Pay {html.escape(cid)}: '
        f"{html.escape(str(cases[cid].dispute.amount))}</button> "
        f"{html.escape(cases[cid].title)}</p>"
        for cid in waiting
    )
    # JSON inside a <script> block: escape "<" so no value can close the tag.
    data = json.dumps(intents).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Nirnaya kit 4 test payments</title>
<script src="{CHECKOUT_SDK}"></script></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto">
<h1>Kit 4 test payments (Airwallex sandbox)</h1>
<p>Pay each case on Airwallex's hosted checkout with the test card
<b>4035 5010 0000 0008</b>, any future expiry, any CVC. After each payment, come back to this
page for the next one. Then run the sandbox runner again.</p>
{buttons}
<script>
const intents = {data};
const pay = async (b) => {{
  const i = intents[b.dataset.case];
  const {{ payments }} = await window.AirwallexComponentsSDK.init({{
    env: "demo", enabledElements: ["payments"] }});
  payments.redirectToCheckout({{ env: "demo", mode: "payment", currency: i.currency,
    intent_id: i.intent_id, client_secret: i.client_secret,
    successUrl: "https://www.example.com/success" }});
}};
document.querySelectorAll("button[data-case]").forEach((b) =>
  b.addEventListener("click", () => pay(b)));
</script></body></html>
"""


def wait_until_readable(
    source: Any,
    ids: list[str],
    sleep: Callable[[float], None],
    tries: int = 10,  # noqa: ANN401
) -> None:
    """A new sandbox dispute may briefly report an undocumented status; wait it out."""
    for dispute_id in ids:
        for attempt in range(tries):
            try:
                source.retrieve_dispute(M, dispute_id)
                break
            except AirwallexError as e:
                if e.code != "unmappable_dispute" or attempt == tries - 1:
                    raise
                sleep(1.0)


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    confirm: Callable[[], bool] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="nirnaya.sandbox_demo", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--offline", action="store_true", help="use the in-memory fake")
    parser.add_argument("--submit", action="store_true", help="send real accept/challenge")
    parser.add_argument("--yes", action="store_true", help="skip the --submit confirmation")
    parser.add_argument("--fresh", action="store_true", help="stage new disputes")
    parser.add_argument(
        "--checkout",
        action="store_true",
        help="pay on Airwallex's hosted checkout page instead of the native card API",
    )
    args = parser.parse_args(argv)

    if env is None:
        load_dotenv()
        env = dict(os.environ)
    settings = Settings.from_env(env)
    if args.submit and settings.dry_run:
        out("--submit needs DRY_RUN=false in .env for this run.")
        return 2
    if not args.submit and not settings.dry_run:
        out("DRY_RUN=false without --submit: refusing. Use --submit, or set DRY_RUN=true.")
        return 2
    if args.submit and not args.yes:
        ask = confirm or (
            lambda: input("Type SUBMIT to send real writes to the sandbox: ") == "SUBMIT"
        )
        if not ask():
            out("Not confirmed; nothing sent.")
            return 1

    now = now or datetime.now(UTC)
    cases = {c.id: c for c in load_cases()}
    if args.offline:
        fake = FakeAirwallex()
        inner: Any = fake
        sim: SandboxSimulator = FakeSimulator(fake, now=lambda: now)
        state_path, db = None, ":memory:"
    else:
        creds = Credentials.from_env(env)
        http = AirwallexHttp(creds)
        inner = AirwallexClient(http, merchant_id=M)
        sim = AirwallexSimulator(http, merchant_id=M, base_url=creds.base_url)
        state_path, db = DATA_DIR / "sandbox_state.json", str(DATA_DIR / "sandbox.db")
        DATA_DIR.mkdir(exist_ok=True)

    state = RunState.load(state_path, args.fresh, now)
    if args.fresh and not args.offline:
        db = str(DATA_DIR / f"sandbox-{state.run}.db")
    mode = "offline fake" if args.offline else "Airwallex sandbox"
    out(f"Kit 4 on the {mode}. DRY_RUN={settings.dry_run}. Run {state.run}.")
    out("\n1. Stage the cases (create + pay, raise dispute)")
    waiting = stage(
        sim, state, cases, now, out, checkout=args.checkout, save=lambda: state.save(state_path)
    )
    if waiting:
        page = (DATA_DIR if state_path else Path(".")) / "checkout.html"
        if state_path is not None:
            page.write_text(checkout_page(state, cases, waiting), encoding="utf-8")
        out(f"\nPay {', '.join(waiting)} on the hosted checkout: open {page.resolve()}")
        out("in a browser, pay each case with the test card, then run this again.")
        return 0
    scoped = ScopedAirwallex(inner, {v["dispute"] for v in state.cases.values()})
    wait_until_readable(scoped, sorted(scoped.allowed), sleep)

    store = Store(connect(db))
    store.register_merchant(M, airwallex_account_id="sandbox", policy=DEMO_POLICY,
                            delegated_authority=True)  # fmt: skip
    orch = build_orchestrator(
        store, scoped, scoped, synthetic_world(list(cases.values())), settings, env=env
    )
    out("\n2. Decide and act")
    orch.run_until_idle(now)

    k1 = state.cases["K1"]["dispute"]
    if not state.rejected:
        current = scoped.retrieve_dispute(M, k1)
        if current.status is DisputeStatus.CHALLENGED and current.stage is Stage.RFI:
            out("\n3. The issuer rejects K1's evidence (simulated escalation to CHARGEBACK)")
            sim.escalate(M, k1, due_at=now + RESPONSE_WINDOW)
            state.rejected = True
            state.save(state_path)
            later = now + timedelta(minutes=1)
            wait_until_readable(scoped, [k1], sleep)
            orch.run_until_idle(later)
        else:
            out(f"\n3. Rejection step skipped: K1 is {current.status} (needs a real challenge)")

    out("\n4. Result per case")
    for cid in KIT_CASES:
        dispute_id = state.cases[cid]["dispute"]
        d = scoped.retrieve_dispute(M, dispute_id)
        case = store.case(M, dispute_id)
        out(
            f"\n== {cid}: {cases[cid].title}\n   dispute {dispute_id}: {d.stage} {d.status}, "
            f"{d.amount}, reason {d.reason.type} (code {d.reason.original_code})"
            f"\n   Nirnaya: {case.state if case else 'not ingested'}"
            f"; expected route {cases[cid].expected.route}"
        )
        for line in case_trace(store, M, dispute_id):
            out(line)
    if isinstance(inner, AirwallexClient) and inner.unmappable:
        out(f"\nUnmappable disputes (not processed): {inner.unmappable}")
    out(f"\nAudit chain intact: {verify_chain(store.conn) is None}")
    return 0


def cli() -> int:
    try:
        return main()
    except AirwallexError as e:
        print(f"Airwallex refused the request: {e}", file=sys.stderr)
        print("Nothing further was sent. Staged ids so far are in .nirnaya/.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())

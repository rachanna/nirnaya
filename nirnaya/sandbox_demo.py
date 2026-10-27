"""Kit 4 on the real Airwallex sandbox: stage the three cases, decide, act, handle the rejection.

    uv run python -m nirnaya.sandbox_demo              # stage + dry run (nothing is sent)
    uv run python -m nirnaya.sandbox_demo --offline    # the same against the in-memory fake
    uv run python -m nirnaya.sandbox_demo --submit     # real accept/challenge (DRY_RUN=false)
    uv run python -m nirnaya.sandbox_demo --fresh      # stage new disputes instead of reusing

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
from nirnaya.integrations.airwallex.simulation import AirwallexSimulator, SandboxSimulator
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


def stage(
    sim: SandboxSimulator, source: Any, state: RunState, cases: dict[str, EvalCase],  # noqa: ANN401
    now: datetime, out: Callable[[str], None],
) -> None:  # fmt: skip
    for cid in KIT_CASES:
        if cid in state.cases:
            out(f"   {cid}: reusing {state.cases[cid]['dispute']}")
            continue
        d = cases[cid].dispute
        paid = sim.create_payment(
            M, key=f"{state.run}:{cid}", amount=d.amount, merchant_order_id=d.merchant_order_id
        )
        dispute_id = sim.create_dispute(
            M,
            payment_intent_id=paid.payment_intent_id,
            reason_code=d.reason.original_code or "",
            stage=d.stage,
            due_at=now + RESPONSE_WINDOW,
        )
        state.cases[cid] = {"intent": paid.payment_intent_id, "dispute": dispute_id}
        out(
            f"   {cid}: payment {paid.payment_intent_id} ({paid.status}), "
            f"dispute {dispute_id} at {d.stage}, Visa {d.reason.original_code}"
        )


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
    out("\n1. Stage the cases (create + confirm payment, raise dispute)")
    stage(sim, inner, state, cases, now, out)
    state.save(state_path)
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


if __name__ == "__main__":
    sys.exit(main())

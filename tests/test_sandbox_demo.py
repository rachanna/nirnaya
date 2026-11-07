"""The kit 4 sandbox runner, offline: staging, decisions, rejection cycle, safety rules."""

from datetime import UTC, datetime

import pytest

from nirnaya.integrations.airwallex.scoped import ScopedAirwallex
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models import AcceptReason
from nirnaya.orchestrator.ports import AirwallexError
from nirnaya.sandbox_demo import main
from tests.factories import MERCHANT, make_dispute

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def run(*argv, dry_run="true", confirm=None):
    lines: list[str] = []
    code = main(
        list(argv),
        env={"DRY_RUN": dry_run, "NIRNAYA_AGENT": "scripted"},
        out=lines.append,
        sleep=lambda _: None,
        now=NOW,
        confirm=confirm,
    )
    return code, "\n".join(lines)


def test_offline_dry_run_reaches_the_kit_routes_and_sends_nothing():
    code, text = run("--offline")
    assert code == 0
    assert "write: CHALLENGE cycle 1 DRY_RUN (dry run: not sent)" in text
    assert "write: ACCEPT cycle 1 DRY_RUN (dry run: not sent)" in text
    assert "UNANSWERED_CUSTOMER_CONTACT" in text
    assert "Rejection step skipped" in text
    assert "Audit chain intact: True" in text


def test_offline_submit_handles_the_rejection_with_a_second_challenge():
    code, text = run("--offline", "--submit", "--yes", dry_run="false")
    assert code == 0
    assert "The issuer rejects K1's evidence" in text
    assert "write: CHALLENGE cycle 1 CONFIRMED" in text
    assert "write: CHALLENGE cycle 2 CONFIRMED" in text
    assert "write: ACCEPT cycle 1 CONFIRMED" in text
    assert "CHARGEBACK CHALLENGED" in text  # K1 after the second challenge


def test_writes_need_both_dry_run_false_and_submit():
    assert run("--offline", dry_run="false")[0] == 2  # DRY_RUN=false alone is refused
    assert run("--offline", "--submit", "--yes")[0] == 2  # --submit with DRY_RUN=true too


def test_submit_without_confirmation_sends_nothing():
    code, text = run("--offline", "--submit", dry_run="false", confirm=lambda: False)
    assert code == 1 and "nothing sent" in text


def test_the_scope_hides_and_protects_disputes_the_runner_did_not_stage():
    aw = FakeAirwallex()
    aw.add_dispute(make_dispute(id="mine"))
    aw.add_dispute(make_dispute(id="theirs"))
    scoped = ScopedAirwallex(aw, {"mine"})
    assert [d.id for d in scoped.list_disputes(MERCHANT)] == ["mine"]
    with pytest.raises(AirwallexError, match="not_staged"):
        scoped.accept(MERCHANT, "theirs", request_id="r", reason=AcceptReason.OTHERS)
    with pytest.raises(AirwallexError, match="not_staged"):
        scoped.retrieve_dispute(MERCHANT, "theirs")


def test_checkout_mode_stops_until_the_browser_payments_are_made():
    code, text = run("--offline", "--checkout")
    assert code == 0
    assert "Pay K1, K2, K3 on the hosted checkout" in text
    assert "2. Decide and act" not in text  # nothing is decided before the payments exist


def test_staging_resumes_after_payment_without_creating_anything_twice():
    from nirnaya.evaluation.cases import load_cases
    from nirnaya.integrations.mocks.simulator_fake import FakeSimulator
    from nirnaya.sandbox_demo import RunState, stage

    cases = {c.id: c for c in load_cases()}
    sim = FakeSimulator(FakeAirwallex(), now=lambda: NOW)
    state = RunState(run="r")
    saves: list[int] = []

    def go():
        return stage(sim, state, cases, NOW, lambda _: None, checkout=True,
                     save=lambda: saves.append(1))  # fmt: skip

    assert go() == ["K1", "K2", "K3"]  # intents created, all waiting for payment
    intents = {cid: st["intent"] for cid, st in state.cases.items()}
    for intent in intents.values():
        sim.pay(intent)  # the shopper pays each on the hosted checkout
    assert go() == []
    assert {cid: st["intent"] for cid, st in state.cases.items()} == intents  # same intents
    assert all("dispute" in st and "secret" not in st for st in state.cases.values())
    assert go() == []  # a third run reuses everything
    assert len(saves) == 6  # three intents, three disputes: each saved once


def test_checkout_page_uses_the_documented_hosted_checkout_and_escapes_values():
    from nirnaya.evaluation.cases import load_cases
    from nirnaya.sandbox_demo import CHECKOUT_SDK, RunState, checkout_page

    cases = {c.id: c for c in load_cases()}
    state = RunState(run="r", cases={"K1": {"intent": "int_1", "secret": "s</script><x>"}})
    page = checkout_page(state, cases, ["K1"])
    assert CHECKOUT_SDK in page
    assert 'env: "demo"' in page and "redirectToCheckout" in page and "intent_id" in page
    assert "</script><x>" not in page  # a value can never close the script block
    assert "4035 5010 0000 0008" in page


def test_a_stale_unpaid_intent_is_replaced_with_a_new_request_id():
    from datetime import timedelta

    from nirnaya.evaluation.cases import load_cases
    from nirnaya.integrations.mocks.simulator_fake import FakeSimulator
    from nirnaya.sandbox_demo import RunState, stage

    cases = {c.id: c for c in load_cases()}
    sim = FakeSimulator(FakeAirwallex(), now=lambda: NOW)
    state = RunState(run="r")

    def go(at):
        return stage(sim, state, cases, at, lambda _: None, checkout=True, save=lambda: None)

    go(NOW)
    first = state.cases["K1"]["intent"]
    go(NOW + timedelta(hours=2))  # the checkout secret has expired, still unpaid
    second = state.cases["K1"]["intent"]
    assert second != first  # a new intent: the fake, like Airwallex, keys on request_id
    assert state.cases["K1"]["attempt"] == "2"


def test_a_real_run_refuses_cases_a_dry_run_already_took_through(monkeypatch):
    """FC-09: rehearsing on staged disputes then running --submit used to send nothing."""
    from nirnaya import sandbox_demo
    from nirnaya.store import Store, connect

    store = Store(connect())
    state = sandbox_demo.RunState(run="r", cases={"K2": {"dispute": "D"}})

    class Row:
        status = "DRY_RUN"

    monkeypatch.setattr(store, "writes", lambda m, d: [Row()])
    assert sandbox_demo._rehearsed(store, state) == ["K2"]
    monkeypatch.setattr(store, "writes", lambda m, d: [])
    assert sandbox_demo._rehearsed(store, state) == []


@pytest.mark.parametrize(
    ("status", "in_doubt"),
    [
        ("UNKNOWN", True),
        ("PENDING", True),
        ("CONFIRMED", False),
        ("DRY_RUN", False),
        ("FAILED", False),
    ],
)
def test_only_a_write_that_may_have_landed_is_in_doubt(monkeypatch, status, in_doubt):
    from nirnaya import sandbox_demo
    from nirnaya.store import Store, connect

    store = Store(connect())
    state = sandbox_demo.RunState(run="r", cases={"K1": {"dispute": "D"}})

    class Row:
        pass

    Row.status = status
    monkeypatch.setattr(store, "writes", lambda m, d: [Row()])
    assert sandbox_demo._in_doubt(store, state) == (["K1"] if in_doubt else [])


def test_a_write_in_doubt_fails_the_run(monkeypatch):
    """The 7 Nov live run exited 0 with K1's challenge unresolved."""
    from nirnaya import sandbox_demo

    fake = FakeAirwallex()
    fake.fail_next("challenge", "timeout_before_apply")
    monkeypatch.setattr(sandbox_demo, "FakeAirwallex", lambda: fake)
    code, text = run("--offline", "--submit", "--yes", dry_run="false")
    assert code == 1
    assert "write: CHALLENGE cycle 1 UNKNOWN" in text
    assert "K1 may or may not have landed" in text


def test_a_state_file_from_an_earlier_run_keeps_using_sandbox_db(tmp_path):
    from nirnaya.sandbox_demo import RunState

    path = tmp_path / "state.json"
    path.write_text('{"run": "r", "cases": {}}', encoding="utf-8")
    assert RunState.load(path, False, NOW).db == "sandbox.db"
    assert RunState.load(tmp_path / "none.json", False, NOW).db == "sandbox.db"


def test_a_fresh_run_and_its_resume_share_one_ledger(monkeypatch, tmp_path):
    """FC-27: --fresh wrote to sandbox-<run>.db and the next run opened sandbox.db, so a dry run
    was missed and the record of writes was split."""
    from nirnaya import sandbox_demo
    from nirnaya.integrations.mocks.simulator_fake import FakeSimulator

    fake = FakeAirwallex()

    class Client:
        def __new__(cls, *_a, **_k):
            return fake

    monkeypatch.setattr(sandbox_demo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sandbox_demo, "AirwallexHttp", lambda _c: None)
    monkeypatch.setattr(sandbox_demo, "AirwallexClient", Client)
    monkeypatch.setattr(
        sandbox_demo, "AirwallexSimulator", lambda *_a, **_k: FakeSimulator(fake, now=lambda: NOW)
    )

    def go(*argv, dry_run):
        lines: list[str] = []
        env = {
            "DRY_RUN": dry_run,
            "NIRNAYA_AGENT": "scripted",
            "AIRWALLEX_CLIENT_ID": "c",
            "AIRWALLEX_API_KEY": "k",
        }
        code = main(list(argv), env=env, out=lines.append, sleep=lambda _: None, now=NOW)
        return code, "\n".join(lines)

    assert go("--fresh", dry_run="true")[0] == 0
    code, text = go("--submit", "--yes", dry_run="false")
    assert code == 1 and "ran as a dry run" in text
    run_id = NOW.strftime("%Y%m%dT%H%M%S")
    assert [p.name for p in tmp_path.glob("*.db")] == [f"sandbox-{run_id}.db"]

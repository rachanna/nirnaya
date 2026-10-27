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

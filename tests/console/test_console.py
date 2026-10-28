"""The console's view layer: the kit 4 arc, the review queue, the fabrication check, eval."""

import json

import pytest

from nirnaya.console.session import DemoSession
from nirnaya.console.views import (
    blocked,
    case_detail,
    evaluation,
    fabrication_check,
    kit_card,
    queue,
)
from nirnaya.models import Action

SCRIPTED = {"NIRNAYA_AGENT": "scripted"}


@pytest.fixture
def session():
    s = DemoSession.offline(env=SCRIPTED)
    s.run_kit()
    return s


def test_the_kit_cases_land_where_the_kit_says(session):
    k1, k2, k3 = (kit_card(session, k) for k in ("K1", "K2", "K3"))
    assert k1.cycles[-1].revised.action == "CHALLENGE"
    assert k1.cycles[-1].writes == ("CHALLENGE CONFIRMED",)
    assert (k2.cycles[-1].revised.action, k2.cycles[-1].revised.accept_reason) == (
        "ACCEPT",
        "LOW_VALUE_TRANSACTION",
    )
    assert k3.state == "REFERRED"
    assert k3.cycles[-1].referral == ("UNANSWERED_CUSTOMER_CONTACT",)


def test_k1_tells_the_arc_from_airwallex_data_alone_to_a_challenge(session):
    (arc,) = kit_card(session, "K1").cycles
    # With only the dispute and payment attempt: an RFI accept would refund, so a person decides.
    assert arc.initial.action == "REFER"
    assert "RFI_ACCEPT_NEEDS_APPROVAL" in arc.initial.referral_reasons
    assert any("PRIOR_UNDISPUTED_ORDERS_MATCH" in w for w in arc.initial.worth_finding)
    # New information from the merchant's systems, then the revised decision.
    assert any(info.startswith("get_order(ORD-K1)") for info in arc.new_information)
    # Prior orders alone already justify the challenge, so the search stops there (VoI).
    assert arc.revised.action == "CHALLENGE" and arc.revised.p == "0.60"


def test_k3_new_information_is_the_unanswered_emails(session):
    (arc,) = kit_card(session, "K3").cycles
    assert arc.initial.action == "ACCEPT"
    assert any("K3-email-1" in info for info in arc.new_information)
    assert arc.revised.action == "REFER"


def test_the_issuer_rejection_starts_a_second_cycle_that_is_challenged_again(session):
    session.reject_k1()
    card = kit_card(session, "K1")
    assert [a.stage for a in card.cycles] == ["RFI", "CHARGEBACK"]
    assert card.cycles[1].writes == (
        "CHALLENGE CONFIRMED; customer_info: device dev-k1-7f3a91, ip 203.0.113.24 (from K1-order)",
    )  # at CHARGEBACK the challenge carries the order's device and IP
    assert card.dispute_status == "CHARGEBACK CHALLENGED"


def test_the_fabricated_signature_is_blocked_with_the_reason(session):
    check = fabrication_check(session)
    assert check.outcome == "BLOCKED"
    (claim,) = blocked(check)
    assert claim.source.endswith(".signed_by")
    assert claim.failures


def test_the_queue_shows_k3_and_an_approval_acts_on_it(session):
    (item,) = queue(session)
    assert item.case_id == "K3" and item.reasons == ("UNANSWERED_CUSTOMER_CONTACT",)
    assert item.detail["unanswered_contact_ids"] == ["K3-email-1", "K3-email-2"]
    assert item.hours_left > 0
    session.approve("K3", Action.ACCEPT, approver="ops@example.test")
    assert queue(session) == []
    assert kit_card(session, "K3").cycles[-1].writes == ("ACCEPT CONFIRMED",)


def test_handling_a_referral_manually_closes_it(session):
    session.close("K3", approver="ops@example.test", note="refunded by hand")
    assert kit_card(session, "K3").state == "CLOSED"


def test_case_detail_has_the_timeline_evidence_and_claims(session):
    detail = case_detail(session, "K1")
    assert detail.timeline and detail.evidence
    assert all(e["intact"] for e in detail.evidence)
    assert detail.claims and all(c.status == "VERIFIED" for c in detail.claims)


def test_evaluation_view_reads_the_report(tmp_path):
    path = tmp_path / "report.json"
    assert evaluation(path) is None
    path.write_text(json.dumps({"policies": {"nirnaya": {}}}), encoding="utf-8")
    assert evaluation(path) == {"policies": {"nirnaya": {}}}


def test_a_sandbox_run_is_viewable_but_cannot_act(tmp_path):
    assert DemoSession.from_sandbox_run(tmp_path) is None  # nothing there
    offline = DemoSession.offline(env=SCRIPTED)
    with pytest.raises(RuntimeError):
        DemoSession(offline.store, offline.cases, {}, offline.now).run_kit()

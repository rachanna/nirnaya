from datetime import timedelta

import pytest

from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models import AcceptReason, ChallengeReason, DisputeStatus, ProductType, Stage
from nirnaya.orchestrator.ports import AirwallexError, ChallengeRequest, WriteOutcomeUnknown
from tests.factories import CASE, MERCHANT, OTHER_MERCHANT, T0, make_dispute


def fake(**dispute_overrides):
    aw = FakeAirwallex()
    aw.add_dispute(make_dispute(**dispute_overrides))
    return aw


def challenge(aw, request_id="req-1", files=()):
    request = ChallengeRequest(
        request_id=request_id,
        file_ids=files,
        claim_ids=("c",),
        reason=ChallengeReason.OTHER_REASONS,
        product_type=ProductType.PHYSICAL_GOODS,
    )
    return aw.challenge(MERCHANT, CASE, request)


def test_disputes_are_scoped_by_merchant():
    aw = fake()
    assert [d.id for d in aw.list_disputes(MERCHANT)] == [CASE]
    assert aw.list_disputes(OTHER_MERCHANT) == []
    with pytest.raises(AirwallexError, match="not_found"):
        aw.retrieve_dispute(OTHER_MERCHANT, CASE)


def test_accept_moves_status_and_is_idempotent_per_request_id():
    aw = fake()
    first = aw.accept(MERCHANT, CASE, request_id="r1", reason=AcceptReason.OTHERS)
    assert first.status is DisputeStatus.ACCEPTED
    assert aw.accept(MERCHANT, CASE, request_id="r1", reason=AcceptReason.OTHERS) == first


def test_request_id_reused_for_another_operation_is_rejected():
    aw = fake()
    aw.accept(MERCHANT, CASE, request_id="r1", reason=AcceptReason.OTHERS)
    with pytest.raises(AirwallexError, match="duplicate_request_id"):
        challenge(aw, request_id="r1")


@pytest.mark.parametrize("stage", [Stage.RFI, Stage.CHARGEBACK])
def test_challenge_allowed_at_rfi_and_chargeback(stage):
    assert challenge(fake(stage=stage)).status is DisputeStatus.CHALLENGED


@pytest.mark.parametrize("stage", [Stage.PRE_CHARGEBACK, Stage.PRE_ARBITRATION])
def test_challenge_refused_at_other_stages(stage):
    with pytest.raises(AirwallexError, match="validation_error"):
        challenge(fake(stage=stage))


def test_challenge_refused_unless_awaiting_response():
    with pytest.raises(AirwallexError, match="validation_error"):
        challenge(fake(status=DisputeStatus.CHALLENGED))


def test_challenge_needs_files_uploaded_by_this_merchant():
    aw = fake()
    with pytest.raises(AirwallexError, match="unknown file"):
        challenge(aw, files=("file_9999",))
    fid = aw.upload_file(MERCHANT, filename="evidence.pdf", content=b"%PDF-")
    assert challenge(aw, files=(fid,)).status is DisputeStatus.CHALLENGED


def test_png_evidence_is_rejected():
    with pytest.raises(AirwallexError, match="PNG"):
        fake().upload_file(MERCHANT, filename="scan.png", content=b"x")


def test_pre_chargeback_auto_accept():
    aw = FakeAirwallex(auto_accept_pre_chargeback=True)
    aw.add_dispute(make_dispute(stage=Stage.PRE_CHARGEBACK))
    assert aw.retrieve_dispute(MERCHANT, CASE).status is DisputeStatus.ACCEPTED


def test_escalate_from_rfi_reopens_at_chargeback_and_not_from_chargeback():
    aw = fake(stage=Stage.RFI)
    challenge(aw)
    d = aw.simulate_escalate(CASE, due_at=T0 + timedelta(days=30))
    assert (d.stage, d.status) == (Stage.CHARGEBACK, DisputeStatus.REQUIRES_RESPONSE)
    with pytest.raises(AirwallexError, match="not supported"):
        aw.simulate_escalate(CASE, due_at=T0 + timedelta(days=40))


def test_resolve_sets_won_or_lost():
    assert fake().simulate_resolve(CASE, in_favor_of="MERCHANT").status is DisputeStatus.WON
    assert fake().simulate_resolve(CASE, in_favor_of="CUSTOMER").status is DisputeStatus.LOST


def test_injected_error_leaves_nothing_applied():
    aw = fake()
    aw.fail_next("accept", "error")
    with pytest.raises(AirwallexError):
        aw.accept(MERCHANT, CASE, request_id="r1", reason=AcceptReason.OTHERS)
    assert aw.retrieve_dispute(MERCHANT, CASE).status is DisputeStatus.REQUIRES_RESPONSE


def test_injected_timeout_lands_the_write_but_reports_unknown():
    aw = fake()
    aw.fail_next("accept", "timeout_after_apply")
    with pytest.raises(WriteOutcomeUnknown):
        aw.accept(MERCHANT, CASE, request_id="r1", reason=AcceptReason.OTHERS)
    assert aw.retrieve_dispute(MERCHANT, CASE).status is DisputeStatus.ACCEPTED


def test_payment_intent_ownership():
    aw = FakeAirwallex()
    aw.add_dispute(make_dispute())
    aw.add_dispute(make_dispute(id="case-2", payment_intent_id="int-2"), intent_owner=None)
    assert aw.payment_intent_owner(MERCHANT, "int-1") == MERCHANT
    assert aw.payment_intent_owner(MERCHANT, "int-2") is None
    assert aw.payment_intent_owner(OTHER_MERCHANT, "int-1") is None


def test_a_chargeback_challenge_needs_a_product_type():
    aw = fake(stage=Stage.CHARGEBACK)
    file_id = aw.upload_file(MERCHANT, filename="e.pdf", content=b"%PDF")
    request = ChallengeRequest(
        request_id="r-pt",
        file_ids=(file_id,),
        claim_ids=("c",),
        reason=ChallengeReason.PRODUCT_RECEIVED,
    )
    with pytest.raises(AirwallexError, match="product_type"):
        aw.challenge(MERCHANT, CASE, request)

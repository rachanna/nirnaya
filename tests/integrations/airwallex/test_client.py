"""The client against a scripted wire: exact requests, paging, tenancy, write outcomes."""

import json
from decimal import Decimal

import pytest

from nirnaya.integrations.airwallex.client import AirwallexClient
from nirnaya.integrations.airwallex.http import SANDBOX_API, SANDBOX_FILES
from nirnaya.models import AcceptReason, ChallengeReason, DisputeStatus, ProductType, ThreeDSResult
from nirnaya.orchestrator.ports import AirwallexError, ChallengeRequest, WriteOutcomeUnknown
from tests.integrations.airwallex.helpers import LOGIN, error, ok, session

M = "merchant-demo"
NO_REFUNDS = ok({"items": [], "has_more": False})


def wire_dispute(id_="dst_1", **changes):
    data = {
        "id": id_,
        "stage": "RFI",
        "status": "REQUIRES_RESPONSE",
        "amount": 1500,
        "currency": "USD",
        "merchant_order_id": "ORD-K1",
        "payment_intent_id": "int_1",
        "payment_attempt_id": "att_1",
        "card_brand": "visa",
        "reason": {"original_code": "10.4", "type": "FRAUDULENT"},
        "due_at": "2026-11-10T00:00:00+0000",
        "created_at": "2026-10-27T10:00:00+0000",
    }
    data.update(changes)
    return data


def client(*replies):
    http, transport, _ = session(LOGIN, *replies)
    return AirwallexClient(http, merchant_id=M), transport


def path(url: str) -> str:
    return url.removeprefix(SANDBOX_API)


def test_list_pages_until_there_is_no_page_after_and_nets_refunds():
    c, t = client(
        ok({"items": [wire_dispute("dst_1")], "page_after": "abc"}),
        ok({"items": [{"amount": 100, "currency": "USD", "status": "SETTLED"}], "has_more": False}),
        ok({"items": [wire_dispute("dst_2")]}),
        NO_REFUNDS,
    )
    disputes = c.list_disputes(M)
    assert [d.id for d in disputes] == ["dst_1", "dst_2"]
    assert disputes[0].refunded_amount.amount == Decimal("100")
    lists = [path(u) for m, u in t.calls() if "payment_disputes" in u]
    assert lists == [
        "/api/v1/pa/payment_disputes?page=0&size=100",
        "/api/v1/pa/payment_disputes?page=abc&size=100",
    ]
    refunds = [path(u) for m, u in t.calls() if "refunds" in u]
    assert refunds[0] == "/api/v1/pa/refunds?payment_intent_id=int_1&page_num=0&page_size=100"


def test_an_empty_sandbox_lists_nothing():
    c, _ = client(ok({"items": []}))
    assert c.list_disputes(M) == []


def test_an_unmappable_dispute_is_surfaced_not_dropped_silently():
    c, _ = client(
        ok({"items": [wire_dispute(status="RECEIVED"), wire_dispute("dst_2")]}), NO_REFUNDS
    )
    disputes = c.list_disputes(M)
    assert [d.id for d in disputes] == ["dst_2"]
    assert "undocumented value 'RECEIVED'" in c.unmappable["dst_1"]


def test_retrieve_maps_one_dispute_and_refuses_an_unmappable_one():
    c, t = client(ok(wire_dispute()), NO_REFUNDS)
    d = c.retrieve_dispute(M, "dst_1")
    assert (d.id, d.status) == ("dst_1", DisputeStatus.REQUIRES_RESPONSE)
    assert path(t.sent[1].url) == "/api/v1/pa/payment_disputes/dst_1"
    c, _ = client(ok(wire_dispute(stage="MEDIATION")))
    with pytest.raises(AirwallexError, match="unmappable_dispute"):
        c.retrieve_dispute(M, "dst_1")


def test_a_client_refuses_another_merchant_before_any_request():
    c, t = client()
    with pytest.raises(AirwallexError, match="wrong_merchant"):
        c.list_disputes("merchant-other")
    with pytest.raises(AirwallexError, match="wrong_merchant"):
        c.accept("merchant-other", "dst_1", request_id="r", reason=AcceptReason.OTHERS)
    assert t.sent == []


def test_payment_attempt_maps_and_is_none_when_not_found():
    attempt = {
        "id": "att_1",
        "payment_intent_id": "int_1",
        "amount": 1500,
        "currency": "USD",
        "created_at": "2026-10-27T10:00:00+0000",
        "authentication_data": {"avs_result": "pass", "cvc_result": "pass", "ds_data": {}},
    }
    c, t = client(ok(attempt))
    a = c.payment_attempt(M, "att_1")
    assert a.three_ds is ThreeDSResult.NOT_PERFORMED
    assert path(t.sent[1].url) == "/api/v1/pa/payment_attempts/att_1"
    c, _ = client(error(404, "not_found"))
    assert c.payment_attempt(M, "att_x") is None


def test_payment_intent_owner_is_this_merchant_only_for_its_own_account():
    c, _ = client(ok({"id": "int_1"}))
    assert c.payment_intent_owner(M, "int_1") == M
    c, _ = client(ok({"id": "int_1", "connected_account_id": "acct_other"}))
    assert c.payment_intent_owner(M, "int_1") is None
    c, _ = client(error(404, "not_found"))
    assert c.payment_intent_owner(M, "int_1") is None


def test_accept_sends_the_documented_body():
    c, t = client(ok(wire_dispute(stage="CHARGEBACK", status="ACCEPTED")))
    d = c.accept(M, "dst_1", request_id="req-1", reason=AcceptReason.LOW_VALUE_TRANSACTION)
    call = t.sent[1]
    assert (call.method, path(call.url)) == ("POST", "/api/v1/pa/payment_disputes/dst_1/accept")
    assert call.json() == {
        "request_id": "req-1",
        "reason": "LOW_VALUE_TRANSACTION",
        "accepted_by": "nirnaya",
    }
    assert d.status is DisputeStatus.ACCEPTED


def challenge_request(**changes):
    data = dict(
        request_id="req-2",
        file_ids=("file_1",),
        claim_ids=("c1", "c2"),
        reason=ChallengeReason.PURCHASE_HISTORY,
        product_type=None,
    )
    data.update(changes)
    return ChallengeRequest(**data)


def test_challenge_sends_standard_with_the_reason_and_the_primary_document():
    c, t = client(ok(wire_dispute(status="CHALLENGED")))
    c.challenge(M, "dst_1", challenge_request())
    body = t.sent[1].json()
    assert path(t.sent[1].url) == "/api/v1/pa/payment_disputes/dst_1/challenge"
    assert body["request_id"] == "req-2"
    assert body["challenge_method"] == "STANDARD"
    assert body["reason"] == "PURCHASE_HISTORY"
    assert "product_type" not in body  # RFI: not sent
    (doc,) = body["supporting_documents"]["documents"]
    assert doc["type"] == "PRIMARY" and doc["file_ids"] == ["file_1"]
    # Nothing structured that the verifier did not clear.
    assert not {"delivery_info", "customer_info", "order_info", "evidence"} & set(body)


def test_challenge_at_chargeback_sends_product_type():
    c, t = client(ok(wire_dispute(stage="CHARGEBACK", status="CHALLENGED")))
    c.challenge(M, "dst_1", challenge_request(product_type=ProductType.PHYSICAL_GOODS))
    assert t.sent[1].json()["product_type"] == "PHYSICAL_GOODS"


def test_a_write_whose_response_cannot_be_read_is_an_unknown_outcome():
    c, _ = client(ok({"status": "SOMETHING_ELSE"}))
    with pytest.raises(WriteOutcomeUnknown):
        c.accept(M, "dst_1", request_id="r", reason=AcceptReason.OTHERS)


def test_a_rejected_write_is_an_error_not_an_unknown_outcome():
    c, _ = client(error(400, "validation_error", "Dispute transition is not supported"))
    with pytest.raises(AirwallexError, match="validation_error"):
        c.challenge(M, "dst_1", challenge_request())


def test_upload_posts_pdf_to_the_files_host_and_returns_the_file_id():
    c, t = client(ok({"file_id": "file_9", "filename": "e.pdf"}, status=201))
    assert c.upload_file(M, filename="evidence.pdf", content=b"%PDF-1.4") == "file_9"
    assert t.sent[1].url == f"{SANDBOX_FILES}/api/v1/files/upload"


def test_upload_refuses_anything_but_pdf_and_trims_long_names():
    c, t = client(ok({"file_id": "f"}, status=201))
    with pytest.raises(AirwallexError, match="only PDF"):
        c.upload_file(M, filename="scan.png", content=b"x")
    c.upload_file(M, filename="x" * 60 + ".pdf", content=b"%PDF")
    disposition = t.sent[-1].body.split(b"filename=")[1].split(b'"')[1]
    assert len(disposition) == 50 and disposition.endswith(b".pdf")


def test_bodies_are_json():
    c, t = client(ok(wire_dispute(status="ACCEPTED")))
    c.accept(M, "dst_1", request_id="r", reason=AcceptReason.OTHERS)
    assert t.sent[1].headers["Content-Type"] == "application/json"
    json.loads(t.sent[1].body)

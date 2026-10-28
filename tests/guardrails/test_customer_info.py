"""customer_info on a chargeback challenge: device ID and IP, only from verified merchant data."""

from nirnaya.guardrails.customer_info import customer_info
from nirnaya.models import RecordAuthor, SourceSystem, Stage
from tests.factories import make_record

ORDER = {"order_id": "ORD-1", "device_id": "dev-k1-7f3a91", "ip_address": "203.0.113.24"}


def order(fields=ORDER, **kw):
    kw.setdefault("author", RecordAuthor.MERCHANT)
    return make_record(SourceSystem.ORDER, dict(fields), id=kw.pop("id", "order-1"), **kw)


def test_both_values_come_from_the_cited_order_record_at_chargeback():
    rec = order()
    info = customer_info(Stage.CHARGEBACK, [rec], cited={"order-1"})
    assert (info.device_id, info.ip, info.source_record_id) == (
        "dev-k1-7f3a91",
        "203.0.113.24",
        "order-1",
    )
    assert info.source_sha256 == rec.sha256
    assert info.wire() == {"device_id": "dev-k1-7f3a91", "ip": "203.0.113.24"}


def test_nothing_is_sent_outside_chargeback():
    for stage in (Stage.RFI, Stage.PRE_CHARGEBACK, Stage.PRE_ARBITRATION):
        assert customer_info(stage, [order()], cited={"order-1"}) is None


def test_an_uncited_record_supplies_nothing():
    assert customer_info(Stage.CHARGEBACK, [order()], cited=set()) is None
    assert customer_info(Stage.CHARGEBACK, [order()], cited={"carrier-1"}) is None


def test_only_the_merchants_own_order_record_counts():
    customer = order(author=RecordAuthor.CUSTOMER)
    assert customer_info(Stage.CHARGEBACK, [customer], cited={"order-1"}) is None
    carrier = make_record(SourceSystem.CARRIER, ORDER, id="order-1")
    assert customer_info(Stage.CHARGEBACK, [carrier], cited={"order-1"}) is None


def test_a_bad_value_is_left_out_never_repaired():
    bad_ip = order({**ORDER, "ip_address": "203.0.113.999"})
    info = customer_info(Stage.CHARGEBACK, [bad_ip], cited={"order-1"})
    assert (info.device_id, info.ip) == ("dev-k1-7f3a91", None)
    assert info.wire() == {"device_id": "dev-k1-7f3a91"}
    neither = order({"order_id": "ORD-1", "device_id": "  ", "ip_address": 42})
    assert customer_info(Stage.CHARGEBACK, [neither], cited={"order-1"}) is None
    too_long = order({**ORDER, "device_id": "d" * 129})
    assert customer_info(Stage.CHARGEBACK, [too_long], cited={"order-1"}).device_id is None
    ipv6 = order({**ORDER, "ip_address": "2001:db8::1"})
    assert customer_info(Stage.CHARGEBACK, [ipv6], cited={"order-1"}).ip == "2001:db8::1"


def test_a_tampered_record_supplies_nothing():
    rec = order()
    tampered = rec.model_copy(update={"fields": {**ORDER, "ip_address": "198.51.100.7"}})
    assert customer_info(Stage.CHARGEBACK, [tampered], cited={"order-1"}) is None


def test_the_client_sends_customer_info_only_when_present():
    from nirnaya.integrations.airwallex.client import AirwallexClient
    from nirnaya.models import ChallengeReason
    from nirnaya.orchestrator.ports import ChallengeRequest
    from tests.factories import MERCHANT

    sent = []

    class Http:
        def post(self, path, payload, *, write=False):
            sent.append(payload)
            return {"id": "dsp_1"}

    client = AirwallexClient(Http(), merchant_id=MERCHANT)
    client._written = lambda body, dispute_id: body  # the dispute mapping is tested elsewhere
    info = customer_info(Stage.CHARGEBACK, [order()], cited={"order-1"})
    base = dict(
        request_id="r", file_ids=("f",), claim_ids=("c",), reason=ChallengeReason.PURCHASE_HISTORY
    )
    client.challenge(MERCHANT, "dsp_1", ChallengeRequest(**base, customer_info=info))
    client.challenge(MERCHANT, "dsp_1", ChallengeRequest(**base))
    assert sent[0]["customer_info"] == {"device_id": "dev-k1-7f3a91", "ip": "203.0.113.24"}
    assert "customer_info" not in sent[1]

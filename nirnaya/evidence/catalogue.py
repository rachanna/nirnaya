"""What each record field can establish, what each reason requires, what contradicts what.

ASSUMPTION: these tables are Nirnaya's documented expert policy for the demo, written
against the field names of our own SYNTHETIC mock systems (order, carrier, login,
helpdesk, policy store) and our payment-attempt snapshot. They are not card-network
rules and must be reviewed before use with real merchants.

Customer-authored records never establish anything: their text is untrusted data.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from nirnaya.models.claim import DateTimeValue, DateValue, MoneyValue, NormalisedValue, TextValue
from nirnaya.models.enums import AmountBasis, EvidenceType, ReasonCategory, RecordAuthor
from nirnaya.models.enums import SourceSystem as S
from nirnaya.models.evidence import EvidenceRecord, FieldValue

E = EvidenceType
Fields = Mapping[str, FieldValue]
Predicate = Callable[[Fields], bool]

TRUSTED_AUTHORS = frozenset({RecordAuthor.MERCHANT, RecordAuthor.THIRD_PARTY})


def has(name: str) -> Predicate:
    return lambda f: f.get(name) not in (None, "")


def equals(name: str, value: FieldValue) -> Predicate:
    return lambda f: name in f and f[name] == value and type(f[name]) is type(value)


def one_of(name: str, *values: str) -> Predicate:
    return lambda f: f.get(name) in values


def int_above(name: str, threshold: int) -> Predicate:
    def check(f: Fields) -> bool:
        v = f.get(name)
        return isinstance(v, int) and not isinstance(v, bool) and v > threshold

    return check


def int_at_least(name: str, minimum: int) -> Predicate:
    def check(f: Fields) -> bool:
        v = f.get(name)
        return isinstance(v, int) and not isinstance(v, bool) and v >= minimum

    return check


def positive_amount(name: str) -> Predicate:
    def check(f: Fields) -> bool:
        v = f.get(name)
        if not isinstance(v, str):
            return False
        try:
            return Decimal(v) > 0
        except InvalidOperation:
            return False

    return check


def both(a: Predicate, b: Predicate) -> Predicate:
    return lambda f: a(f) and b(f)


@dataclass(frozen=True)
class FieldRule:
    """Citing `field` of a `source_system` record establishes `establishes` when `when` holds."""

    source_system: S
    field: str
    establishes: EvidenceType
    when: Predicate
    description: str


_delivered = equals("status", "DELIVERED")

FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(S.CARRIER, "status", E.DELIVERY_CONFIRMED, _delivered, "carrier status DELIVERED"),
    FieldRule(
        S.CARRIER, "delivered_at", E.DELIVERY_CONFIRMED, _delivered, "delivery scan timestamp"
    ),
    FieldRule(
        S.CARRIER, "signed_by", E.DELIVERY_SIGNATURE, both(_delivered, has("signed_by")),
        "name captured at signature",
    ),
    FieldRule(
        S.PAYMENT_ATTEMPT, "three_ds", E.THREE_DS_AUTHENTICATED,
        equals("three_ds", "AUTHENTICATED"), "3DS authenticated",
    ),
    FieldRule(S.PAYMENT_ATTEMPT, "avs", E.AVS_MATCH, equals("avs", "MATCH"), "AVS full match"),
    FieldRule(S.PAYMENT_ATTEMPT, "cvc", E.CVC_MATCH, equals("cvc", "MATCH"), "CVC match"),
    FieldRule(
        S.LOGIN, "purchase_device_login_at", E.ACCOUNT_LOGIN_MATCH,
        has("purchase_device_login_at"), "login from the purchasing device",
    ),
    FieldRule(
        S.LOGIN, "active_after_renewal_at", E.USAGE_AFTER_RENEWAL,
        has("active_after_renewal_at"), "account activity after renewal",
    ),
    FieldRule(
        S.ORDER, "prior_undisputed_orders_matching_device_ip", E.PRIOR_UNDISPUTED_ORDERS_MATCH,
        int_at_least("prior_undisputed_orders_matching_device_ip", 2),
        "at least two earlier undisputed orders from the same device and IP",
    ),
    FieldRule(S.ORDER, "order_id", E.ORDER_PLACED, has("order_id"), "order exists"),
    FieldRule(S.ORDER, "placed_at", E.ORDER_PLACED, has("placed_at"), "order timestamp"),
    FieldRule(S.ORDER, "total_amount", E.ORDER_PLACED, has("total_amount"), "order total"),
    FieldRule(S.ORDER, "customer_name", E.ORDER_PLACED, has("customer_name"), "order name"),
    FieldRule(
        S.ORDER, "order_id", E.DISTINCT_TRANSACTIONS, equals("is_distinct_purchase", True),
        "order is a separate purchase, not a duplicate",
    ),
    FieldRule(
        S.ORDER, "refunded_amount", E.REFUND_ISSUED, positive_amount("refunded_amount"),
        "refund issued on the order",
    ),
    FieldRule(
        S.POLICY, "cancellation_terms_accepted_at", E.CANCELLATION_TERMS_ACCEPTED,
        has("cancellation_terms_accepted_at"), "customer accepted cancellation terms",
    ),
    FieldRule(
        S.HELPDESK, "contacts_before_dispute", E.NO_PRIOR_CUSTOMER_CONTACT,
        equals("contacts_before_dispute", 0), "no support contact before the dispute",
    ),
)  # fmt: skip


def record_establishes(record: EvidenceRecord, field: str) -> frozenset[EvidenceType]:
    """The facts that citing `field` of `record` establishes. Empty if the field is absent."""
    if record.author not in TRUSTED_AUTHORS:
        return frozenset()
    if record.fields.get(field) in (None, ""):
        return frozenset()
    return frozenset(
        rule.establishes
        for rule in FIELD_RULES
        if rule.source_system is record.source_system
        and rule.field == field
        and rule.when(record.fields)
    )


# Facts that only make sense for one reason. With an UNKNOWN reason they may not be claimed.
REASON_SPECIFIC: Mapping[EvidenceType, ReasonCategory] = {
    E.DELIVERY_CONFIRMED: ReasonCategory.NON_RECEIPT,
    E.DELIVERY_SIGNATURE: ReasonCategory.NON_RECEIPT,
    E.THREE_DS_AUTHENTICATED: ReasonCategory.FRAUD,
    E.AVS_MATCH: ReasonCategory.FRAUD,
    E.CVC_MATCH: ReasonCategory.FRAUD,
    E.ACCOUNT_LOGIN_MATCH: ReasonCategory.FRAUD,
    E.PRIOR_UNDISPUTED_ORDERS_MATCH: ReasonCategory.FRAUD,
    E.DISTINCT_TRANSACTIONS: ReasonCategory.DUPLICATE,
    E.CANCELLATION_TERMS_ACCEPTED: ReasonCategory.SUBSCRIPTION,
    E.USAGE_AFTER_RENEWAL: ReasonCategory.SUBSCRIPTION,
}

# Evidence a submission must establish, per reason. Every group must be satisfied by at
# least one of its members (an AND of ORs).
REQUIRED_EVIDENCE: Mapping[ReasonCategory, tuple[frozenset[EvidenceType], ...]] = {
    ReasonCategory.NON_RECEIPT: (frozenset({E.DELIVERY_CONFIRMED}),),
    ReasonCategory.FRAUD: (
        frozenset(
            {
                E.THREE_DS_AUTHENTICATED,
                E.AVS_MATCH,
                E.ACCOUNT_LOGIN_MATCH,
                E.PRIOR_UNDISPUTED_ORDERS_MATCH,
            }
        ),
    ),
    ReasonCategory.DUPLICATE: (frozenset({E.DISTINCT_TRANSACTIONS, E.REFUND_ISSUED}),),
    ReasonCategory.SUBSCRIPTION: (
        frozenset({E.CANCELLATION_TERMS_ACCEPTED}),
        frozenset({E.USAGE_AFTER_RENEWAL, E.NO_PRIOR_CUSTOMER_CONTACT}),
    ),
    ReasonCategory.UNKNOWN: (),
}


@dataclass(frozen=True)
class ContradictionRule:
    """A `source_system` record for which `when` holds contradicts `contradicts`."""

    contradicts: EvidenceType
    source_system: S
    when: Predicate
    description: str


CONTRADICTION_RULES: tuple[ContradictionRule, ...] = (
    ContradictionRule(
        E.DELIVERY_CONFIRMED, S.CARRIER,
        one_of("status", "RETURNED_TO_SENDER", "LOST", "DELIVERY_EXCEPTION"),
        "carrier reports the parcel was not delivered",
    ),
    ContradictionRule(
        E.DELIVERY_CONFIRMED, S.ORDER, equals("fulfilment_status", "CANCELLED"),
        "order was cancelled before fulfilment",
    ),
    ContradictionRule(
        E.DELIVERY_CONFIRMED, S.HELPDESK, equals("acknowledged_non_delivery", True),
        "support acknowledged the parcel did not arrive",
    ),
    ContradictionRule(
        E.DELIVERY_CONFIRMED, S.HELPDESK, equals("reported_not_received", True),
        "customer reported the parcel did not arrive",
    ),
    ContradictionRule(
        E.DELIVERY_SIGNATURE, S.CARRIER, equals("signature_status", "NOT_SIGNED"),
        "carrier reports no signature was captured",
    ),
    ContradictionRule(
        E.THREE_DS_AUTHENTICATED, S.PAYMENT_ATTEMPT, one_of("three_ds", "FAILED", "NOT_PERFORMED"),
        "a payment attempt shows 3DS failed or was not performed",
    ),
    ContradictionRule(
        E.AVS_MATCH, S.PAYMENT_ATTEMPT, equals("avs", "NO_MATCH"),
        "a payment attempt shows an AVS mismatch",
    ),
    ContradictionRule(
        E.CVC_MATCH, S.PAYMENT_ATTEMPT, equals("cvc", "NO_MATCH"),
        "a payment attempt shows a CVC mismatch",
    ),
    ContradictionRule(
        E.ACCOUNT_LOGIN_MATCH, S.LOGIN, equals("account_compromise_reported", True),
        "the account was reported compromised",
    ),
    ContradictionRule(
        E.PRIOR_UNDISPUTED_ORDERS_MATCH, S.LOGIN, equals("account_compromise_reported", True),
        "the account was reported compromised",
    ),
    ContradictionRule(
        E.ACCOUNT_LOGIN_MATCH, S.HELPDESK, equals("reported_unauthorised", True),
        "customer reported the transaction as unauthorised",
    ),
    ContradictionRule(
        E.DISTINCT_TRANSACTIONS, S.ORDER, has("duplicate_of_order_id"),
        "an order is marked as a duplicate of another",
    ),
    ContradictionRule(
        E.REFUND_ISSUED, S.ORDER, equals("refund_status", "FAILED"),
        "the refund failed",
    ),
    ContradictionRule(
        E.USAGE_AFTER_RENEWAL, S.LOGIN, equals("cancelled_before_renewal", True),
        "the subscription was cancelled before renewal",
    ),
    ContradictionRule(
        E.NO_PRIOR_CUSTOMER_CONTACT, S.HELPDESK, int_above("contacts_before_dispute", 0),
        "support logged customer contact before the dispute",
    ),
)  # fmt: skip


def contradiction_rules_matching(record: EvidenceRecord) -> list[ContradictionRule]:
    """Every contradiction rule that holds for this record (the one place rules are matched)."""
    return [
        rule
        for rule in CONTRADICTION_RULES
        if rule.source_system is record.source_system and rule.when(record.fields)
    ]


def contradiction_rules_for(record: EvidenceRecord, fact: EvidenceType) -> list[ContradictionRule]:
    return [rule for rule in contradiction_rules_matching(record) if rule.contradicts is fact]


class Anchor(StrEnum):
    """What a dated field is compared with."""

    # placed_at of the record's own order (see TemporalRule): earliest and latest, which differ
    # only when that order has revised versions or the order is not identifiable.
    ORDER_PLACED_EARLIEST = "ORDER_PLACED_EARLIEST"
    ORDER_PLACED_LATEST = "ORDER_PLACED_LATEST"
    DISPUTE_OPENED = "DISPUTE_OPENED"
    CAPTURED = "CAPTURED"  # the record's own captured_at


@dataclass(frozen=True)
class TemporalRule:
    """A record that establishes one of `facts` must date `field` within its anchors.

    Order anchors come from the record's own order, chosen in this order:
    1. the orders sharing the record's `order_link` field (e.g. the carrier's tracking number);
    2. otherwise the disputed order (`order_id` equal to the dispute's `merchant_order_id`);
    3. otherwise every order on file, leniently (earliest for lower bounds, latest for upper).
    Only trusted, intact orders count; with none, order-anchored bounds are skipped. A clash
    means the merchant's records disagree.
    """

    facts: frozenset[EvidenceType]
    source_system: S
    field: str
    label: str
    not_before: tuple[Anchor, ...] = ()
    not_after: tuple[Anchor, ...] = ()
    # Dated after the dispute opened: still evidence, but the approver is told.
    note_if_late: bool = False
    # A field the record shares with its order, linking it to that order (None: the disputed one).
    order_link: str | None = None


_ORDER_FACTS = frozenset(
    {E.ORDER_PLACED, E.DISTINCT_TRANSACTIONS, E.REFUND_ISSUED, E.PRIOR_UNDISPUTED_ORDERS_MATCH}
)

TEMPORAL_RULES: tuple[TemporalRule, ...] = (
    TemporalRule(
        frozenset({E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE}), S.CARRIER, "delivered_at",
        "delivery scan", (Anchor.ORDER_PLACED_EARLIEST,), (Anchor.CAPTURED,), note_if_late=True,
        order_link="tracking_number",
    ),
    TemporalRule(
        frozenset({E.USAGE_AFTER_RENEWAL}), S.LOGIN, "active_after_renewal_at",
        "usage after renewal", (Anchor.ORDER_PLACED_EARLIEST,), (Anchor.CAPTURED,),
        note_if_late=True,
    ),
    TemporalRule(
        frozenset({E.CANCELLATION_TERMS_ACCEPTED}), S.POLICY, "cancellation_terms_accepted_at",
        "cancellation terms acceptance", (), (Anchor.ORDER_PLACED_LATEST, Anchor.CAPTURED),
    ),
    TemporalRule(
        frozenset({E.ACCOUNT_LOGIN_MATCH}), S.LOGIN, "purchase_device_login_at",
        "purchase-device login", (), (Anchor.CAPTURED,), note_if_late=True,
    ),
    TemporalRule(
        _ORDER_FACTS, S.ORDER, "placed_at", "order placed",
        (), (Anchor.DISPUTE_OPENED, Anchor.CAPTURED),
    ),
)  # fmt: skip


# Claims of these facts deny that the customer complained. They are blocked whenever a
# customer-authored record predates the dispute: the draft must not deny the complaint exists.
DENIAL_FACTS = frozenset({E.NO_PRIOR_CUSTOMER_CONTACT})

CLAIM_TEMPLATES: Mapping[EvidenceType, str] = {
    E.ORDER_PLACED: "Order record",
    E.DELIVERY_CONFIRMED: "Delivery confirmed by carrier",
    E.DELIVERY_SIGNATURE: "Delivery signed for by",
    E.THREE_DS_AUTHENTICATED: "3-D Secure authentication result",
    E.AVS_MATCH: "Address verification result",
    E.CVC_MATCH: "Card security code check result",
    E.ACCOUNT_LOGIN_MATCH: "Account login from the purchasing device at",
    E.PRIOR_UNDISPUTED_ORDERS_MATCH: "Prior undisputed orders from the same device and IP",
    E.DISTINCT_TRANSACTIONS: "Separate purchase, order",
    E.REFUND_ISSUED: "Refund issued",
    E.CANCELLATION_TERMS_ACCEPTED: "Cancellation terms accepted at",
    E.USAGE_AFTER_RENEWAL: "Account active after renewal at",
    E.NO_PRIOR_CUSTOMER_CONTACT: "Customer support contacts before the dispute",
}


def format_value(value: NormalisedValue) -> str:
    match value:
        case MoneyValue(money=money, basis=basis):
            suffix = " net of refunds" if basis is AmountBasis.NET_OF_REFUNDS else ""
            return f"{money}{suffix}"
        case DateTimeValue(at=at):
            return at.isoformat().replace("+00:00", "Z")
        case DateValue(date=date, tz=tz):
            return f"{date.isoformat()} ({tz})"
        case TextValue(text=text):
            return text
    raise TypeError(f"unhandled value {value!r}")


def render_claim_text(fact: EvidenceType, value: NormalisedValue) -> str:
    """The only text a claim may carry: a fixed template plus its normalised value."""
    return f"{CLAIM_TEMPLATES[fact]}: {format_value(value)}"

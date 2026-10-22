"""Internal domain enums.

These are Nirnaya's own vocabulary, not Airwallex wire values. Mapping Airwallex stage,
status, reason-code and payment-attempt result strings onto them happens in the
integration layer (Milestone 5). TODO(verify-airwallex-docs): build that mapping from
the published API reference, not from memory.
"""

from enum import StrEnum


class Stage(StrEnum):
    PRE_CHARGEBACK = "PRE_CHARGEBACK"
    CHARGEBACK = "CHARGEBACK"
    RFI = "RFI"
    PRE_ARBITRATION = "PRE_ARBITRATION"
    ARBITRATION = "ARBITRATION"


AUTOMATED_STAGES = frozenset({Stage.PRE_CHARGEBACK, Stage.CHARGEBACK})


class DisputeStatus(StrEnum):
    AWAITING_RESPONSE = "AWAITING_RESPONSE"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class ReasonCategory(StrEnum):
    FRAUD = "FRAUD"
    NON_RECEIPT = "NON_RECEIPT"
    DUPLICATE = "DUPLICATE"
    SUBSCRIPTION = "SUBSCRIPTION"
    # Mastercard Collaboration can hide the reason. Never auto-challenged.
    UNKNOWN = "UNKNOWN"


KNOWN_REASONS = frozenset(r for r in ReasonCategory if r is not ReasonCategory.UNKNOWN)


class EvidenceType(StrEnum):
    """The facts a record can establish. Priors, claims and requirements all use this set."""

    ORDER_PLACED = "ORDER_PLACED"
    DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"
    DELIVERY_SIGNATURE = "DELIVERY_SIGNATURE"
    THREE_DS_AUTHENTICATED = "THREE_DS_AUTHENTICATED"
    AVS_MATCH = "AVS_MATCH"
    CVC_MATCH = "CVC_MATCH"
    ACCOUNT_LOGIN_MATCH = "ACCOUNT_LOGIN_MATCH"
    DISTINCT_TRANSACTIONS = "DISTINCT_TRANSACTIONS"
    REFUND_ISSUED = "REFUND_ISSUED"
    CANCELLATION_TERMS_ACCEPTED = "CANCELLATION_TERMS_ACCEPTED"
    USAGE_AFTER_RENEWAL = "USAGE_AFTER_RENEWAL"
    NO_PRIOR_CUSTOMER_CONTACT = "NO_PRIOR_CUSTOMER_CONTACT"


class SourceSystem(StrEnum):
    ORDER = "ORDER"
    CARRIER = "CARRIER"
    LOGIN = "LOGIN"
    HELPDESK = "HELPDESK"
    POLICY = "POLICY"
    PAYMENT_ATTEMPT = "PAYMENT_ATTEMPT"


class RecordAuthor(StrEnum):
    """Who wrote the record. Customer-authored records are untrusted and never support a claim."""

    MERCHANT = "MERCHANT"
    CUSTOMER = "CUSTOMER"
    THIRD_PARTY = "THIRD_PARTY"


class ThreeDSResult(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    ATTEMPTED = "ATTEMPTED"
    FAILED = "FAILED"
    NOT_PERFORMED = "NOT_PERFORMED"
    UNKNOWN = "UNKNOWN"


class AvsResult(StrEnum):
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    NO_MATCH = "NO_MATCH"
    NOT_CHECKED = "NOT_CHECKED"
    UNKNOWN = "UNKNOWN"


class CvcResult(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    NOT_CHECKED = "NOT_CHECKED"
    UNKNOWN = "UNKNOWN"


class Action(StrEnum):
    ACCEPT = "ACCEPT"
    CHALLENGE = "CHALLENGE"
    REFER = "REFER"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    # Supported by its record, but another merchant record disagrees: a human decides.
    CONFLICT = "CONFLICT"


class AmountBasis(StrEnum):
    GROSS = "GROSS"
    NET_OF_REFUNDS = "NET_OF_REFUNDS"

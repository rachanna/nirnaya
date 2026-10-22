"""Domain enums.

Stage, DisputeStatus, CardBrand and AcceptReason carry Airwallex's wire values exactly, as
documented in the Payment Disputes API reference
(https://www.airwallex.com/docs/api/payments/payment_disputes/retrieve, checked 2026-10-23).
An undocumented value fails validation rather than being guessed at.

ReasonCategory, EvidenceType and the payment-attempt result enums are Nirnaya's own
vocabulary. TODO(verify-airwallex-docs): map payment-attempt 3DS/AVS/CVC wire values in M5.
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
    REQUIRES_RESPONSE = "REQUIRES_RESPONSE"
    CHALLENGED = "CHALLENGED"
    PENDING_CLOSURE = "PENDING_CLOSURE"
    PENDING_DECISION = "PENDING_DECISION"
    ACCEPTED = "ACCEPTED"
    REVERSED = "REVERSED"
    WON = "WON"
    LOST = "LOST"
    EXPIRED = "EXPIRED"


# The only status in which the merchant may accept or challenge.
ACTIONABLE_STATUS = DisputeStatus.REQUIRES_RESPONSE


class CardBrand(StrEnum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    MAESTRO = "maestro"
    UNION_PAY = "union pay"
    AMERICAN_EXPRESS = "american express"
    JCB = "jcb"
    DINERS_CLUB_INTERNATIONAL = "diners club international"
    DISCOVER = "discover"


class AcceptReason(StrEnum):
    """Values the accept request may send (the response set is wider)."""

    AGREEMENT_REACHED_WITH_CUSTOMER = "AGREEMENT_REACHED_WITH_CUSTOMER"
    CUSTOMER_RELATIONSHIP_MAINTENANCE = "CUSTOMER_RELATIONSHIP_MAINTENANCE"
    LOW_VALUE_TRANSACTION = "LOW_VALUE_TRANSACTION"
    VALID_CUSTOMER_DISPUTE = "VALID_CUSTOMER_DISPUTE"
    COLLABORATION_ACCEPTED_MANUAL = "COLLABORATION_ACCEPTED_MANUAL"
    OTHERS = "OTHERS"


class ReasonCategory(StrEnum):
    """Nirnaya's decision category, derived from Airwallex reason.type (see models.reasons)."""

    FRAUD = "FRAUD"
    NON_RECEIPT = "NON_RECEIPT"
    DUPLICATE = "DUPLICATE"
    SUBSCRIPTION = "SUBSCRIPTION"
    # Reason hidden, unmapped or ambiguous. Never auto-challenged.
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

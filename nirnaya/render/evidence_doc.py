"""The evidence document submitted with a challenge: verified claims only, each with its source.

Card-brand file limits are from Airwallex's "Handle disputes using our APIs" page (checked
2026-10-23). MB is taken as 1,000,000 bytes, the stricter reading.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import CardBrand, ValidationStatus
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.render.pdf import text_pdf
from nirnaya.verifier.result import VerificationResult

MB = 1_000_000


@dataclass(frozen=True)
class BrandLimits:
    extensions: frozenset[str]
    per_file: Mapping[str, int]  # by extension; missing means only the total applies
    total: int


_VISA = BrandLimits(
    frozenset({"pdf", "jpg", "jpeg", "tif", "tiff"}),
    {"pdf": 2 * MB, "jpg": 10 * MB, "jpeg": 10 * MB, "tif": 10 * MB, "tiff": 10 * MB},
    15 * MB,
)
LIMITS: Mapping[CardBrand, BrandLimits] = {
    CardBrand.VISA: _VISA,
    # Kit 4 guide: Airwallex accepts JPG and PDF evidence and rejects PNG, whatever the brand.
    CardBrand.MASTERCARD: BrandLimits(
        frozenset({"pdf", "jpg", "jpeg", "tif", "tiff"}), {}, int(14.5 * MB)
    ),
    CardBrand.AMERICAN_EXPRESS: BrandLimits(
        frozenset({"pdf", "jpg", "jpeg", "tif", "tiff"}),
        dict.fromkeys(("pdf", "jpg", "jpeg", "tif", "tiff"), 4 * MB),
        900 * MB,
    ),
}
# Brands whose limits are not yet copied (TODO(verify-airwallex-docs)) get Visa's, the strictest.
DEFAULT_LIMITS = _VISA


class RenderError(ValueError):
    pass


def check_limits(card_brand: CardBrand | None, files: list[tuple[str, bytes]]) -> None:
    limits = LIMITS.get(card_brand, DEFAULT_LIMITS) if card_brand else DEFAULT_LIMITS
    total = 0
    for name, content in files:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext not in limits.extensions:
            raise RenderError(f"{name}: .{ext} is not accepted for {card_brand}")
        cap = limits.per_file.get(ext)
        if cap is not None and len(content) > cap:
            raise RenderError(f"{name}: {len(content)} bytes exceeds {cap} for {card_brand}")
        total += len(content)
    if total > limits.total:
        raise RenderError(f"{total} bytes exceeds the {limits.total} total for {card_brand}")


def render_evidence(
    dispute: Dispute,
    verification: VerificationResult,
    records: list[EvidenceRecord],
    *,
    cycle: int,
    draft: int,
) -> tuple[str, bytes]:
    """One PDF of every VERIFIED claim with its source excerpt and capture hash."""
    verified = [
        c.claim
        for c in verification.claims
        if c.claim.validation_status is ValidationStatus.VERIFIED
    ]
    if verification.outcome.value != "PASSED" or not verified:
        raise RenderError("only a PASSED verification with verified claims can be rendered")
    by_id = {r.id: r for r in records}
    lines = [
        "Dispute evidence summary",
        "SYNTHETIC DATA - Airwallex sandbox only",
        "",
        f"Dispute: {dispute.id}    Merchant: {dispute.merchant_id}",
        f"Stage: {dispute.stage}    Reason: {dispute.reason.type or 'unknown'}"
        f" ({dispute.reason.original_code or 'no code'})",
        f"Amount: {dispute.amount}    Merchant order: {dispute.merchant_order_id or '-'}",
        "",
        "Each statement below was checked against the record it cites before submission.",
        "",
    ]
    for n, claim in enumerate(verified, start=1):
        record = by_id[claim.source_record_id]
        lines += [
            f"{n}. {claim.text}",
            f"   Source: {record.source_system} record {record.id}, field {claim.source_field},"
            f" value {claim.extracted_value!r}",
            f"   Captured {record.captured_at.isoformat()}; SHA-256 {record.sha256}",
            "",
        ]
    content = text_pdf(lines)
    filename = f"evidence-{dispute.id}-c{cycle}-d{draft}.pdf"
    check_limits(dispute.card_brand, [(filename, content)])
    return filename, content

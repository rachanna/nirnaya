"""The real Airwallex client behind DisputeSource and DisputeActions (architecture section 9.2).

Endpoints and bodies are from the API reference and the kit 4 guide (checked 2026-10-27):
- GET  /api/v1/pa/payment_disputes            (paging: `page`, `size`; next page: `page_after`)
- GET  /api/v1/pa/payment_disputes/{id}
- POST /api/v1/pa/payment_disputes/{id}/accept     {request_id, reason, accepted_by}
- POST /api/v1/pa/payment_disputes/{id}/challenge  {request_id, challenge_method, challenged_by,
       reason, product_type, customer_info, supporting_documents.documents[...]}
- GET  /api/v1/pa/payment_attempts/{id}, GET /api/v1/pa/payment_intents/{id}
- GET  /api/v1/pa/refunds?payment_intent_id=       (paging: `page_num`, `page_size`, `has_more`)
- POST https://files.sandbox.airwallex.com/api/v1/files/upload (multipart `file`, `notes`)

TODO(verify-airwallex-docs): the paging parameter names above are from the API reference as
read on 2026-10-27, not observed on a multi-page sandbox listing; the error code "not_found"
for a missing attempt or intent; and connected_account_id as the sign that an intent belongs
to a connected account (any other value refers the case for ownership).

One client serves one merchant: its credentials, its account. Calls for any other merchant
are refused before they reach the wire.
"""

from typing import Any
from urllib.parse import quote

from nirnaya.integrations.airwallex.http import SANDBOX_FILES, AirwallexHttp
from nirnaya.integrations.airwallex.mapping import (
    UnmappableRecord,
    attempt_from_wire,
    dispute_from_wire,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AcceptReason
from nirnaya.models.payment import PaymentAttempt
from nirnaya.orchestrator.ports import AirwallexError, ChallengeRequest, WriteOutcomeUnknown

PAGE_SIZE = 100
MAX_PAGES = 50  # 5,000 disputes: a guard against a paging loop, not a business limit
ACTOR = "nirnaya"
# Airwallex allows 50 characters for an uploaded file's name.
MAX_FILENAME = 50


class AirwallexClient:
    def __init__(self, http: AirwallexHttp, *, merchant_id: str) -> None:
        self._http = http
        self.merchant_id = merchant_id
        # Disputes the mapping refused, with why: surfaced, never silently dropped.
        self.unmappable: dict[str, str] = {}
        self._unmappable_due: dict[str, str | None] = {}

    def _own(self, merchant_id: str) -> None:
        if merchant_id != self.merchant_id:
            raise AirwallexError("wrong_merchant", "this client serves another merchant")

    @staticmethod
    def _path(template: str, id_: str) -> str:
        return template.format(quote(id_, safe=""))

    # --- DisputeSource ------------------------------------------------------------------------

    def list_disputes(self, merchant_id: str) -> list[Dispute]:
        self._own(merchant_id)
        disputes, page = [], "0"
        for _ in range(MAX_PAGES):
            body = self._http.get("/api/v1/pa/payment_disputes", {"page": page, "size": PAGE_SIZE})
            for item in body.get("items", []):
                if (dispute := self._map(item)) is not None:
                    disputes.append(dispute)
            after = body.get("page_after")
            if not isinstance(after, str) or not after or not body.get("items"):
                return disputes
            page = after
        raise AirwallexError("paging_limit", f"more than {MAX_PAGES} pages of disputes")

    def retrieve_dispute(self, merchant_id: str, dispute_id: str) -> Dispute:
        self._own(merchant_id)
        body = self._http.get(self._path("/api/v1/pa/payment_disputes/{}", dispute_id))
        dispute = self._map(body)
        if dispute is None:
            raise AirwallexError("unmappable_dispute", self.unmappable[dispute_id])
        return dispute

    def payment_attempt(self, merchant_id: str, attempt_id: str) -> PaymentAttempt | None:
        self._own(merchant_id)
        try:
            body = self._http.get(self._path("/api/v1/pa/payment_attempts/{}", attempt_id))
        except AirwallexError as e:
            if e.code == "not_found":
                return None
            raise
        try:
            return attempt_from_wire(body, merchant_id=merchant_id)
        except UnmappableRecord:
            return None  # no attempt evidence rather than a guessed one

    def payment_intent_owner(self, merchant_id: str, intent_id: str) -> str | None:
        """This merchant if the intent is on its own account; None (refer) otherwise."""
        self._own(merchant_id)
        try:
            body = self._http.get(self._path("/api/v1/pa/payment_intents/{}", intent_id))
        except AirwallexError as e:
            if e.code == "not_found":
                return None
            raise
        if body.get("id") != intent_id or body.get("connected_account_id"):
            return None
        return merchant_id

    def _refunds(self, intent_id: str) -> list[dict[str, Any]]:
        refunds: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            body = self._http.get(
                "/api/v1/pa/refunds",
                {"payment_intent_id": intent_id, "page_num": page, "page_size": PAGE_SIZE},
            )
            refunds += [r for r in body.get("items", []) if isinstance(r, dict)]
            if not body.get("has_more"):
                return refunds
        raise AirwallexError("paging_limit", f"more than {MAX_PAGES} pages of refunds")

    def _map(self, item: dict[str, Any]) -> Dispute | None:
        dispute_id = str(item.get("id"))
        try:
            # Validate first, so a dispute that cannot be represented costs no refund lookup.
            dispute = dispute_from_wire(item, merchant_id=self.merchant_id)
            refunds = self._refunds(dispute.payment_intent_id)
            if refunds:
                dispute = dispute_from_wire(item, merchant_id=self.merchant_id, refunds=refunds)
        except UnmappableRecord as e:
            self.unmappable[dispute_id] = str(e)
            due = item.get("due_at")
            self._unmappable_due[dispute_id] = due if isinstance(due, str) else None
            return None
        self.unmappable.pop(dispute_id, None)
        self._unmappable_due.pop(dispute_id, None)
        return dispute

    def refused(self, merchant_id: str) -> dict[str, dict[str, str | None]]:
        """Disputes this merchant has that the mapping could not read, with why and the raw
        due_at, so ingestion can put them in front of a person."""
        self._own(merchant_id)
        return {
            d: {"why": why, "due_at": self._unmappable_due.get(d)}
            for d, why in sorted(self.unmappable.items())
        }

    # --- DisputeActions -----------------------------------------------------------------------

    def accept(
        self, merchant_id: str, dispute_id: str, *, request_id: str, reason: AcceptReason
    ) -> Dispute:
        self._own(merchant_id)
        body = self._http.post(
            self._path("/api/v1/pa/payment_disputes/{}/accept", dispute_id),
            {"request_id": request_id, "reason": reason.value, "accepted_by": ACTOR},
            write=True,
        )
        return self._written(body, dispute_id)

    def challenge(self, merchant_id: str, dispute_id: str, request: ChallengeRequest) -> Dispute:
        self._own(merchant_id)
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            # Always STANDARD: Nirnaya's verified package is what is submitted (section 7.2).
            "challenge_method": "STANDARD",
            # Listed on the challenge reference (checked 2026-10-28).
            # TODO(verify-airwallex-docs): which values challenged_by accepts.
            "challenged_by": ACTOR,
            "reason": request.reason.value,
            "supporting_documents": {
                "documents": [
                    {
                        "type": "PRIMARY",
                        "file_ids": list(request.file_ids),
                        "description": f"Verified evidence: {len(request.claim_ids)} claim(s), "
                        "each bound to a source record.",
                    }
                ]
            },
        }
        if request.product_type is not None:
            payload["product_type"] = request.product_type.value
        if request.customer_info is not None and request.customer_info.wire():
            payload["customer_info"] = request.customer_info.wire()
        body = self._http.post(
            self._path("/api/v1/pa/payment_disputes/{}/challenge", dispute_id), payload, write=True
        )
        return self._written(body, dispute_id)

    def upload_file(self, merchant_id: str, *, filename: str, content: bytes) -> str:
        self._own(merchant_id)
        if not filename.lower().endswith(".pdf"):
            raise AirwallexError("validation_error", "only PDF evidence is sent")
        name = filename if len(filename) <= MAX_FILENAME else filename[-MAX_FILENAME:]
        body = self._http.upload(
            f"{SANDBOX_FILES}/api/v1/files/upload",
            filename=name,
            content=content,
            notes="Nirnaya evidence",
        )
        file_id = body.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise AirwallexError("upload_failed", "no file_id in the upload response")
        return file_id

    def _written(self, body: dict[str, Any], dispute_id: str) -> Dispute:
        """The dispute as the write left it. The write succeeded, so nothing here may report a
        failure: an unreadable response is an unknown outcome, reconciled by re-fetching."""
        try:
            return dispute_from_wire(body, merchant_id=self.merchant_id)
        except UnmappableRecord as e:
            raise WriteOutcomeUnknown(f"{dispute_id}: write accepted, response unreadable") from e

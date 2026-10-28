"""The agent reads notes and proposes quoted evidence; a person confirms it before it counts.

Scenario: a 120 USD not-received chargeback. The carrier record shows DELIVERED but no
structured signature, so p = 0.60 and the maths says accept. The driver's note says the
parcel was handed over and a signature captured: with that, p = 0.80 and the maths says
challenge. The model's reading never decides: it can only send the case to a person with the
exact quote, and a confirmed quote is then checked word for word by the verifier.
"""

from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.evaluation.cases import synthetic_world
from nirnaya.evidence.catalogue import Quote, render_claim_text
from nirnaya.models import Action, EvidenceType
from nirnaya.models.claim import Claim, QuoteValue
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, reject_findings
from nirnaya.orchestrator.states import CaseState
from nirnaya.store.repository import StoreError
from nirnaya.verifier import CheckName, verify
from tests.harness import Harness
from tests.notes import QUOTE, noted_case

E = EvidenceType


CASE = noted_case()
M, CID = CASE.dispute.merchant_id, CASE.dispute.id


class Proposing:
    """A reader that proposes fixed quotes (stands in for the model)."""

    def __init__(self, *proposals):
        self.proposals, self.calls = proposals, []

    def read(self, dispute, facts, records):
        self.calls.append(sorted(facts))
        run = AgentRun(kind="READ", provider="fake", model="fake", turns=1, stop="TOOL_USE")
        return ReadResult(proposals=self.proposals, run=run)


def signature(quote=QUOTE):
    return Quote("NOTED-carrier", "driver_notes", E.DELIVERY_SIGNATURE, quote)


def run(reader=None, case=CASE):
    h = Harness()
    h.stage(case)
    kwargs = {} if reader is None else {"reader": reader}
    orch = Orchestrator(
        h.store, h.aw, h.aw, synthetic_world([case]), ScriptedInvestigator(), ScriptedDrafter(),
        h.settings, **kwargs,
    )  # fmt: skip
    orch.run_until_idle(h.now(case))
    return h, orch


def actions(h):
    return [(w.action, w.status) for w in h.store.writes(M, CID)]


def test_without_a_reader_the_case_is_accepted_as_before():
    h, _ = run()
    assert actions(h) == [("ACCEPT", "DRY_RUN")]


def test_a_verbatim_quote_that_would_flip_the_decision_goes_to_a_person():
    reader = Proposing(signature())
    h, _ = run(reader)
    assert E.DELIVERY_SIGNATURE in reader.calls[0]  # read only for facts that would matter
    assert h.store.case(M, CID).state == CaseState.REFERRED
    assert actions(h) == []  # the model's reading decided nothing
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["PROPOSED_EVIDENCE"]
    (proposed,) = referral.detail["proposed_evidence"]
    assert proposed["quote"] == QUOTE and proposed["fact"] == "DELIVERY_SIGNATURE"
    assert (referral.detail["p_now"], referral.detail["p_if_confirmed"]) == ("0.60", "0.80")
    (finding,) = h.store.findings(M, CID, 1)
    assert finding.status == "PROPOSED"


def test_a_quote_that_is_not_in_the_note_is_dropped_and_changes_nothing():
    h, _ = run(Proposing(signature("signed for by the customer personally")))
    assert actions(h) == [("ACCEPT", "DRY_RUN")]
    assert h.store.findings(M, CID, 1) == []
    (read,) = [r for r in h.store.agent_runs(M, CID, 1) if r["kind"] == "READ"]
    assert read["dropped"]  # recorded, not silently ignored


def test_once_a_person_confirms_the_quote_the_challenge_cites_it_and_verifies():
    h, orch = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    approve(h.store, M, CID, Action.CHALLENGE, approver="ops@example.test", now=h.now(CASE),
            confirm_findings=[finding.finding_id])  # fmt: skip
    orch.run_until_idle(h.now(CASE))
    assert actions(h) == [("CHALLENGE", "DRY_RUN")]
    draft = h.store.latest_draft(M, CID, 1)
    quotes = [c for c in h.store.claims(M, CID, 1, draft) if c.value_kind == "QUOTE"]
    assert [c.extracted_value for c in quotes] == [QUOTE]
    result = h.store.verification(M, CID, 1, draft)
    assert E.DELIVERY_SIGNATURE in result.established
    assert h.store.findings(M, CID, 1)[0].status == "CONFIRMED"


def test_a_rejected_quote_stays_out_and_the_case_stays_with_a_person():
    h, _ = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    reject_findings(h.store, M, CID, [finding.finding_id], approver="ops@example.test",
                    now=h.now(CASE))  # fmt: skip
    assert h.store.findings(M, CID, 1)[0].status == "REJECTED"
    assert h.store.case(M, CID).state == CaseState.REFERRED


def test_a_confirmed_quote_is_still_checked_word_for_word():
    """Confirmation is about meaning; the verifier still checks the words and the record."""
    h, _ = run(Proposing(signature()))
    records = h.store.evidence(M, CID)
    wrong = "signature captured on the handheld unit"  # not what the note says
    claim = Claim(
        id="q", case_id=CID, merchant_id=M, source_record_id="NOTED-carrier",
        source_field="driver_notes", establishes=E.DELIVERY_SIGNATURE, extracted_value=wrong,
        normalised_value=QuoteValue(text=wrong),
        text=render_claim_text(E.DELIVERY_SIGNATURE, QuoteValue(text=wrong)),
    )  # fmt: skip
    result = verify(CASE.dispute, [claim], records, confirmed_quotes=frozenset({signature(wrong)}))
    assert CheckName.QUOTE_NOT_IN_RECORD in {f.check for f in result.claims[0].failures}


def test_notes_are_not_read_when_no_note_could_change_the_decision():
    small = noted_case(case_id="SMALL", amount="50.00")  # even a signature would not pay
    reader = Proposing(Quote("SMALL-carrier", "driver_notes", E.DELIVERY_SIGNATURE, QUOTE))
    h, _ = run(reader, small)
    assert reader.calls == []
    assert [(w.action, w.status) for w in h.store.writes(M, "SMALL")] == [("ACCEPT", "DRY_RUN")]


def test_confirmed_quotes_only_support_a_challenge():
    h, _ = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    try:
        approve(h.store, M, CID, Action.ACCEPT, approver="ops@example.test", now=h.now(CASE),
                confirm_findings=[finding.finding_id])  # fmt: skip
    except ValueError:
        pass
    else:
        raise AssertionError("confirming a quote to accept must be refused")
    assert h.store.findings(M, CID, 1)[0].status == "PROPOSED"


def test_approving_the_original_decision_rejects_the_unconfirmed_quotes():
    h, orch = run(Proposing(signature()))
    approve(h.store, M, CID, Action.ACCEPT, approver="ops@example.test", now=h.now(CASE))
    orch.run_until_idle(h.now(CASE))
    assert h.store.findings(M, CID, 1)[0].status == "REJECTED"
    assert actions(h) == [("ACCEPT", "DRY_RUN")]


def test_an_unknown_finding_cannot_be_confirmed():
    h, _ = run(Proposing(signature()))
    try:
        approve(h.store, M, CID, Action.CHALLENGE, approver="ops@example.test", now=h.now(CASE),
                confirm_findings=["merchant-demo:NOTED:finding:99"])  # fmt: skip
    except StoreError:
        pass
    else:
        raise AssertionError("an unknown finding must be refused")


def test_findings_are_scoped_to_their_merchant():
    h, _ = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    assert h.store.findings("another-merchant", CID, 1) == []
    try:
        h.store.resolve_finding("another-merchant", CID, finding.finding_id, "CONFIRMED",
                                by="x", at=h.now(CASE))  # fmt: skip
    except StoreError:
        pass
    else:
        raise AssertionError("another merchant must not resolve this finding")
    assert h.store.confirmed_quotes("another-merchant", CID, 1) == frozenset()


def test_a_record_changed_after_approval_stops_the_challenge():
    h, orch = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    approve(h.store, M, CID, Action.CHALLENGE, approver="ops@example.test", now=h.now(CASE),
            confirm_findings=[finding.finding_id])  # fmt: skip
    carrier = next(r for r in h.store.evidence(M, CID) if r.id == "NOTED-carrier")
    changed = carrier.model_dump(exclude={"sha256"})
    changed["fields"] = {**carrier.fields, "driver_notes": "Left at the door; no answer."}
    with h.store.transaction():
        h.store.put_evidence_or_revise(M, type(carrier).create(**changed), h.now(CASE))
    orch.run_until_idle(h.now(CASE))
    assert actions(h) == []
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["GUARD_APPROVAL_VOID"]
    assert "evidence_hash" in referral.detail["detail"]


def test_the_evidence_document_marks_a_quote_as_quoted_from_the_note():
    h, orch = run(Proposing(signature()))
    (finding,) = h.store.findings(M, CID, 1)
    approve(h.store, M, CID, Action.CHALLENGE, approver="ops@example.test", now=h.now(CASE),
            confirm_findings=[finding.finding_id])  # fmt: skip
    orch.run_until_idle(h.now(CASE))
    render = h.store.render(M, CID, 1, h.store.latest_draft(M, CID, 1))
    assert b"quoted word for word from the note" in render["content"]
    assert QUOTE.encode() in render["content"]

import re

import pytest

from nirnaya.evaluation.cases import load_cases
from nirnaya.models import CardBrand
from nirnaya.render.evidence_doc import MB, RenderError, check_limits, render_evidence
from nirnaya.render.pdf import LINES_PER_PAGE, text_pdf
from nirnaya.verifier import verify

BY_ID = {c.id: c for c in load_cases()}


def xref_offsets_point_at_objects(pdf: bytes) -> bool:
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
    assert pdf[start:].startswith(b"xref\n")
    entries = re.findall(rb"(\d{10}) 00000 n ", pdf[start:])
    return all(
        pdf[int(off) :].startswith(b"%d 0 obj" % n) for n, off in enumerate(entries, start=1)
    )


# --- PDF writer ------------------------------------------------------------------------------


def test_pdf_has_header_trailer_and_valid_xref():
    pdf = text_pdf(["Hello", "World"])
    assert pdf.startswith(b"%PDF-1.4\n")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert xref_offsets_point_at_objects(pdf)


def test_pdf_is_byte_identical_for_the_same_lines():
    lines = [f"line {i}" for i in range(200)]
    assert text_pdf(lines) == text_pdf(list(lines))


def test_pdf_carries_no_date_or_document_id():
    pdf = text_pdf(["x"])
    assert b"/CreationDate" not in pdf and b"/ID" not in pdf


def test_long_documents_span_pages():
    pdf = text_pdf([f"line {i}" for i in range(LINES_PER_PAGE * 2 + 1)])
    assert b"/Count 3" in pdf
    assert xref_offsets_point_at_objects(pdf)


def test_parentheses_and_backslashes_are_escaped():
    pdf = text_pdf(["a (b) c \\ d"])
    assert b"(a \\(b\\) c \\\\ d) Tj" in pdf


def test_non_latin_text_is_replaced_not_dropped():
    pdf = text_pdf(["name: 山田"])
    assert b"(name: ??) Tj" in pdf


# --- evidence document -----------------------------------------------------------------------


def verified(case_id):
    case = BY_ID[case_id]
    records = case.evidence_records()
    return case, verify(case.dispute, case.claim_objects(), records), records


def test_evidence_document_lists_each_verified_claim_with_its_source():
    case, result, records = verified("K1")
    name, pdf = render_evidence(case.dispute, result, records, cycle=1, draft=1)
    assert name == "evidence-K1-c1-d1.pdf"
    for claim in case.claim_objects():
        assert claim.text.encode("latin-1") in pdf
    for record in records:
        if record.id in {c.source_record_id for c in case.claim_objects()}:
            assert record.sha256.encode() in pdf
    assert b"SYNTHETIC" in pdf


def test_evidence_document_is_deterministic():
    case, result, records = verified("N3")
    first = render_evidence(case.dispute, result, records, cycle=1, draft=1)
    assert render_evidence(case.dispute, result, records, cycle=1, draft=1) == first


def test_blocked_verification_cannot_be_rendered():
    case, result, records = verified("K2C")
    with pytest.raises(RenderError, match="PASSED"):
        render_evidence(case.dispute, result, records, cycle=1, draft=1)


def test_conflicted_verification_cannot_be_rendered():
    case, result, records = verified("N4")
    with pytest.raises(RenderError):
        render_evidence(case.dispute, result, records, cycle=1, draft=1)


# --- card-brand limits -----------------------------------------------------------------------


def test_visa_pdf_over_two_megabytes_is_rejected():
    check_limits(CardBrand.VISA, [("e.pdf", b"x" * 2 * MB)])
    with pytest.raises(RenderError, match="exceeds"):
        check_limits(CardBrand.VISA, [("e.pdf", b"x" * (2 * MB + 1))])


def test_png_is_rejected_for_visa_but_accepted_for_mastercard():
    with pytest.raises(RenderError, match="not accepted"):
        check_limits(CardBrand.VISA, [("scan.png", b"x")])
    check_limits(CardBrand.MASTERCARD, [("scan.png", b"x")])


def test_total_size_is_enforced():
    with pytest.raises(RenderError, match="total"):
        check_limits(CardBrand.MASTERCARD, [(f"{i}.pdf", b"x" * 5 * MB) for i in range(3)])


def test_unknown_brands_get_the_strictest_limits():
    with pytest.raises(RenderError):
        check_limits(CardBrand.JCB, [("e.pdf", b"x" * (2 * MB + 1))])
    with pytest.raises(RenderError):
        check_limits(None, [("scan.png", b"x")])

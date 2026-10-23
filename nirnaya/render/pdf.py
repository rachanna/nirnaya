"""A minimal, deterministic text-only PDF writer (no dependencies).

Same lines in, byte-identical file out: no creation date, no document ID, fixed object order.
Text is Helvetica (WinAnsi); characters outside Latin-1 are replaced with '?'.
"""

import textwrap

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
MARGIN = 50
FONT_SIZE = 10
LEADING = 14
WRAP = 95
LINES_PER_PAGE = (PAGE_HEIGHT - 2 * MARGIN) // LEADING


def _escape(text: str) -> bytes:
    raw = text.encode("latin-1", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        out.extend(textwrap.wrap(line, WRAP, break_long_words=True) or [""])
    return out


def text_pdf(lines: list[str]) -> bytes:
    wrapped = _wrap(lines)
    pages = [wrapped[i : i + LINES_PER_PAGE] for i in range(0, len(wrapped), LINES_PER_PAGE)] or [
        []
    ]

    objects: list[bytes] = []  # object n is objects[n - 1]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # Pages, filled in once the page ids are known
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    page_ids = []
    for page_lines in pages:
        content = [b"BT", f"/F1 {FONT_SIZE} Tf {LEADING} TL".encode(),
                   f"{MARGIN} {PAGE_HEIGHT - MARGIN} Td".encode()]  # fmt: skip
        for line in page_lines:
            content.append(b"(" + _escape(line) + b") Tj T*")
        content.append(b"ET")
        stream = b"\n".join(content)
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        contents_id = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R"
            b" >> >> /Contents %d 0 R >>" % (PAGE_WIDTH, PAGE_HEIGHT, contents_id)
        )
        page_ids.append(len(objects))
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects[1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)

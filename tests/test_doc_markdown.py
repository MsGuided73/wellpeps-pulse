"""Every library document becomes Markdown (owner decision 2026-10-22).

Word and PDF uploads keep their wording; only structure is added (headings,
lists, tables, callouts), so the context harness can split them into whole,
on-topic sections. Plain text and Markdown are kept as they are.
No network, no Claude.
"""

import io
import time
import zipfile

from harvey import doc_markdown, knowledge_docs
from harvey.doc_markdown import Line

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text: str, style: str = "", numbered: bool = False) -> str:
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              ("<w:numPr><w:ilvl w:val=\"0\"/></w:numPr>" if numbered else "") + "</w:pPr>"
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def _tbl(*rows) -> str:
    return "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows) + \
           "</w:tbl>"


def _docx(body: str) -> bytes:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def test_word_headings_lists_tables_and_callouts_become_markdown():
    body = (_p("Compliance Training", "Title") + _p("Training Overview", "Heading1")
            + _p("This training teaches the boundaries.") + _tbl(["Training Objective: recognize what can be answered."])
            + _p("Who Should Complete It", "Heading2") + _p("Employees", "ListBullet") + _p("Contractors", "ListBullet")
            + _p("Steps", "Heading3") + _p("Read the post.", "ListNumber") + _p("Check the rules.", "ListNumber")
            + _p("Bulleted by numbering", numbered=True)
            + _tbl(["Situation", "Response"], ["Dose question", "Use the boundary"]))

    text, extraction = knowledge_docs.extract_text("training.docx", _docx(body))

    assert extraction == "done"
    assert text == (
        "*Compliance Training*\n\n# Training Overview\n\nThis training teaches the boundaries.\n\n"
        "> Training Objective: recognize what can be answered.\n\n## Who Should Complete It\n\n"
        "- Employees\n- Contractors\n\n### Steps\n\n1. Read the post.\n2. Check the rules.\n- Bulleted by numbering\n\n"
        "| Situation | Response |\n|---|---|\n| Dose question | Use the boundary |")


def test_word_entities_and_tabs_are_read_like_text():
    tabbed = '<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t></w:r></w:p>'
    text, _ = knowledge_docs.extract_text("a.docx", _docx(_p("Fish &amp; chips") + tabbed))
    assert text == "Fish & chips\n\nA B"


def test_broken_word_xml_is_read_quickly():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<w:p><w:r><w:t>" * 20000 + "<w:tbl>" * 5000)
    started = time.monotonic()
    knowledge_docs.extract_text("bad.docx", out.getvalue())
    assert time.monotonic() - started < 5


def _page(i, *lines):
    return [Line(i, y, size, bold, text) for (y, size, bold, text) in lines]


def test_pdf_lines_become_markdown_without_running_headers():
    header = (780, 9.0, True, "WELLPEPS | PROTOCOL")
    pages = [
        _page(0, header, (700, 27.0, False, "Social Listening Protocol"), (660, 17.0, True, "1 Governing rules"),
              (640, 10.5, False, "Respond to the unmet need rather than"), (626, 10.5, False, "repeat the provider."),
              (600, 10.5, False, " Never infer clinical failure."), (586, 10.5, False, " Use approved education."),
              (560, 13.0, True, "How to use this module"), (540, 10.5, False, "Apply the sequence first."),
              (30, 9.0, False, "1")),
        _page(1, header, (660, 17.0, True, "2 Inputs"), (640, 10.5, False, "Read the post and its context."),
              (30, 9.0, False, "2")),
        _page(2, header, (660, 17.0, True, "3 Classify intent"), (640, 10.5, False, "Classify what is asked."),
              (30, 9.0, False, "3")),
    ]
    md = doc_markdown.lines_markdown(pages)
    assert md == (
        "*Social Listening Protocol*\n\n# 1 Governing rules\n\nRespond to the unmet need rather than repeat the provider."
        "\n\n- Never infer clinical failure.\n\n- Use approved education.\n\n## How to use this module\n\n"
        "Apply the sequence first.\n\n# 2 Inputs\n\nRead the post and its context.\n\n# 3 Classify intent\n\n"
        "Classify what is asked.")
    assert "WELLPEPS" not in md


def test_a_pdf_the_converter_cannot_read_falls_back_to_plain_text(monkeypatch):
    class Page:
        def extract_text(self):          # no visitor support: the Markdown pass fails
            return "Page one text"

    class Reader:
        def __init__(self, stream):
            self.pages = [Page(), Page()]

    fake = type("pypdf", (), {"PdfReader": Reader})
    monkeypatch.setitem(__import__("sys").modules, "pypdf", fake)
    assert knowledge_docs.extract_text("a.pdf", b"%PDF-1.4") == ("Page one text\n\nPage one text", "done")


def test_markdown_and_text_uploads_are_kept_as_they_are():
    assert knowledge_docs.extract_text("a.md", "# Title\n\n- one".encode()) == ("# Title\n\n- one", "done")

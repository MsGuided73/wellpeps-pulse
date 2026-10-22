"""Library documents as Markdown (owner decision 2026-10-22: every document is converted).

The context harness (harvey/harness.py) splits documents at Markdown headings, so a
document with real headings gives the drafter whole, on-topic sections instead of
page fragments. Wording is never changed: only structure is added.

- Word (.docx): Heading 1/2/3 -> #/##/###, the Title -> an italic first line (not a
  heading, so the harness does not repeat the document name), list paragraphs -> "- "
  or "1.", tables -> Markdown tables, one-cell tables (callout boxes) -> "> " quotes.
  Read with regular expressions and string scanning only, like the plain-text reader:
  no XML parser, so no entity-expansion tricks.
- PDF: the type scale decides. The body size is the size most text uses; bold text
  larger than the body (or any much larger text) becomes a heading, biggest first;
  the largest text seen only on page 1 is the title; lines repeated on many pages
  (running headers, footers) and bare page numbers are dropped; wrapped lines are
  joined into paragraphs; bullet glyphs become "- "; text set side by side on one
  line (table cells) is joined with " | ".
Any failure falls back to the plain text the caller already had.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass

# --- Word ---------------------------------------------------------------------------------------------

_P_OPEN = re.compile(r"<w:p(?:\s[^>]*)?>")
_TBL_OPEN = re.compile(r"<w:tbl(?:\s[^>]*)?>")
_TR_OPEN = re.compile(r"<w:tr(?:\s[^>]*)?>")
_TC_OPEN = re.compile(r"<w:tc(?:\s[^>]*)?>")
_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t\s*>")
_TAB_OR_BREAK = re.compile(r"<w:(?:tab|br|cr)(?:\s[^>]*)?/?>")
_STYLE = re.compile(r'<w:pStyle\s+w:val="([^"]{1,80})"')
_HEADINGS = {"heading1": "#", "heading2": "##", "heading3": "###"}
_BULLETS = ("listbullet", "listparagraph")


def _run_text(xml: str) -> str:
    xml = _TAB_OR_BREAK.sub("<w:t> </w:t>", xml)
    text = "".join(html.unescape(t) for t in _TEXT.findall(xml))
    return re.sub(r"\s+", " ", text).strip()


def _block_end(xml: str, start: int, open_rx: re.Pattern, close: str) -> int:
    """The index just past the element that opens at ``start`` (nested same-name elements counted)."""
    depth, pos = 0, start
    while True:
        nxt_open = open_rx.search(xml, pos)
        nxt_close = xml.find(close, pos)
        if nxt_close < 0:
            return len(xml)
        if nxt_open is not None and nxt_open.start() < nxt_close:
            depth += 1
            pos = nxt_open.end()
            continue
        depth -= 1
        pos = nxt_close + len(close)
        if depth <= 0:
            return pos


def _children(xml: str, open_rx: re.Pattern, close: str) -> list[str]:
    out, pos = [], 0
    while True:
        m = open_rx.search(xml, pos)
        if m is None:
            return out
        end = _block_end(xml, m.start(), open_rx, close)
        out.append(xml[m.start():end])
        pos = end


def _cell(tc: str) -> list[str]:
    """A table cell's paragraphs (a cell can hold several)."""
    paras = [t for t in (_run_text(p) for p in _children(tc, _P_OPEN, "</w:p>")) if t]
    return paras or ([_run_text(tc)] if _run_text(tc) else [])


def _table(xml: str) -> str:
    rows = []
    for tr in _children(xml, _TR_OPEN, "</w:tr>"):
        cells = [_cell(tc) for tc in _children(tr, _TC_OPEN, "</w:tc>")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    if len(rows) == 1 and len(rows[0]) == 1:              # a callout box: keep its paragraphs
        return "\n>\n".join("> " + t for t in rows[0][0])
    rows = [[" ".join(cell).replace("|", "/") for cell in row] for row in rows]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows[1:]])


def docx_markdown(xml: str) -> str:
    """Markdown for word/document.xml ("" when nothing is readable)."""
    blocks: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if items:
            blocks.append("\n".join(items))
            items.clear()

    pos = 0
    while True:
        p, t = _P_OPEN.search(xml, pos), _TBL_OPEN.search(xml, pos)
        if p is None and t is None:
            break
        if t is not None and (p is None or t.start() < p.start()):
            end = _block_end(xml, t.start(), _TBL_OPEN, "</w:tbl>")
            flush()
            md = _table(xml[t.start():end])
            if md:
                blocks.append(md)
            pos = end
            continue
        end = xml.find("</w:p>", p.end())
        end = len(xml) if end < 0 else end + len("</w:p>")
        chunk = xml[p.start():end]
        pos = end
        text = _run_text(chunk)
        if not text:
            continue
        style_m = _STYLE.search(chunk)
        style = (style_m.group(1) if style_m else "").replace(" ", "").lower()
        if style.startswith("listnumber"):
            items.append(f"{sum(1 for i in items if not i.startswith('- ')) + 1}. {text}")
            continue
        if style.startswith(_BULLETS) or "<w:numPr" in chunk:
            items.append("- " + text)
            continue
        flush()
        if style == "title":
            blocks.append(f"*{text}*")
        elif style in _HEADINGS:
            blocks.append(f"{_HEADINGS[style]} {text}")
        else:
            blocks.append(text)
    flush()
    return "\n\n".join(blocks).strip()


# --- PDF ----------------------------------------------------------------------------------------------

# Bullet glyphs, including the Symbol / Wingdings private-use bullets Word exports to PDF.
_BULLET_GLYPHS = ("•", "●", "▪", "◦", "■", "‣", "–", "", "", "", "", "")
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE)


@dataclass
class Line:
    page: int
    y: float
    size: float
    bold: bool
    text: str


def _spans(page) -> list[tuple[float, bool, float, float, str]]:
    """(size, bold, x, y, text) in content-stream order, via pypdf's text visitor."""
    out = []

    def visit(text, cm, tm, fd, fs):
        if not text or not text.strip():
            return
        scale = (tm[3] or 1) * (cm[3] or 1)
        font = str((fd or {}).get("/BaseFont", "")) if fd else ""
        x = cm[4] + tm[4] * (cm[0] or 1)
        y = cm[5] + tm[5] * (cm[3] or 1)
        for part in text.split("\n"):
            if part.strip():
                out.append((round(abs(fs * scale) * 2) / 2, "bold" in font.lower() or "black" in font.lower(),
                            x, y, part))

    page.extract_text(visitor_text=visit)
    return out


def _join(left: str, right: str, plain: str) -> str:
    """"" or " " between two pieces of one line, as pypdf's own plain text spells it (it knows the
    real character widths); a plain space when the plain text doesn't show the boundary."""
    if left.endswith(" ") or right.startswith(" "):
        return ""
    tail, head = left.strip()[-16:], right.strip()[:16]
    if tail and head and (tail + head) in plain:
        return ""
    if tail and head and (tail + " " + head) in plain:
        return " "
    if left.endswith(("-", "—")) or right.startswith(("-", ",", ".", ";", ":", ")")):
        return ""
    return " "


def _lines(page_index: int, spans, plain: str = "") -> list[Line]:
    """Spans on the same baseline become one line; text set far apart on it is joined with " | "."""
    lines: list[Line] = []
    end_x = 0.0
    for size, bold, x, y, text in spans:
        last = lines[-1] if lines else None
        if last is not None and abs(last.y - y) < 1.0:
            gap = x - end_x
            sep = " | " if gap > size * 2.5 and not (last.text.strip()[-16:] + text.strip()[:16]) in plain                 else _join(last.text, text, plain)
            last.text += sep + text
            last.size = max(last.size, size)
            last.bold = last.bold and bold
        else:
            lines.append(Line(page_index, y, size, bold, text))
        end_x = x + len(text) * size * 0.5
    for line in lines:
        line.text = re.sub(r"\s+", " ", line.text).strip()
    out = []
    for line in lines:
        parts = [t.strip() for t in re.split(r"\s*(?=[•●▪])", line.text) if t.strip()]
        out += [Line(line.page, line.y, line.size, line.bold, t) for t in parts] if len(parts) > 1 else [line]
    return [line for line in out if line.text]


def lines_markdown(pages: list[list[Line]]) -> str:
    """Markdown from per-page lines (pure: the PDF reading is in ``pdf_markdown``)."""
    all_lines = [line for page in pages for line in page]
    if not all_lines:
        return ""
    weight = Counter()
    for line in all_lines:
        weight[line.size] += len(line.text)
    body = weight.most_common(1)[0][0]

    # Running headers and footers: the same words on many pages.
    def key(text: str) -> str:
        return re.sub(r"\d+", "#", text.lower())

    edge: set[int] = set()                     # lines that sit at the top or bottom of their page
    for page in pages:
        ordered = sorted(page, key=lambda ln: -ln.y)
        edge.update(id(ln) for ln in ordered[:2] + ordered[-2:])
    on_pages: dict[str, set[int]] = {}
    for line in all_lines:
        if id(line) in edge:
            on_pages.setdefault(key(line.text), set()).add(line.page)
    repeat_min = max(3, int(len(pages) * 0.3))
    repeated = {k for k, ps in on_pages.items() if len(ps) >= repeat_min}
    kept = [ln for ln in all_lines if not (id(ln) in edge and key(ln.text) in repeated)
            and not (_PAGE_NUMBER.match(ln.text) and ln.size <= body)]       # a heading's number stays

    def is_heading(line: Line) -> bool:
        return (line.bold and line.size >= body * 1.1) or line.size >= body * 1.5

    sizes = sorted({ln.size for ln in kept if is_heading(ln)}, reverse=True)
    title_size = sizes[0] if sizes and all(ln.page == 0 for ln in kept if ln.size == sizes[0]) else None
    levels = {s: "#" * (i + 1) for i, s in enumerate(s for s in sizes if s != title_size) if i < 3}

    blocks: list[str] = []
    para: list[Line] = []

    def flush() -> None:
        if para:
            text = ""
            for ln in para:
                text = ln.text if not text else (text + ln.text if text.endswith(("-", "—")) else f"{text} {ln.text}")
            blocks.append(text)
            para.clear()

    previous: Line | None = None
    for ln in kept:
        if ln.size == title_size:
            flush()
            if blocks and blocks[-1].startswith("*") and previous is not None and previous.size == title_size:
                blocks[-1] = blocks[-1][:-1] + " " + ln.text + "*"
            else:
                blocks.append(f"*{ln.text}*")
        elif is_heading(ln):
            flush()
            mark = levels.get(ln.size)
            heading = f"{mark} {ln.text}" if mark else f"**{ln.text}**"
            if (blocks and previous is not None and previous.size == ln.size and is_heading(previous)
                    and previous.page == ln.page and 0 < previous.y - ln.y <= ln.size * 1.6):
                blocks[-1] += " " + ln.text            # a heading wrapped onto two lines
            else:
                blocks.append(heading)
        elif ln.text.startswith(_BULLET_GLYPHS):
            flush()
            blocks.append("- " + ln.text.lstrip("".join(_BULLET_GLYPHS)).strip())
        elif ln.bold and len(ln.text) <= 90:
            flush()
            blocks.append(f"**{ln.text}**")
        else:
            continues = (para and para[-1].page == ln.page and para[-1].size == ln.size
                         and 0 < para[-1].y - ln.y <= ln.size * 1.6)
            if para and not continues:
                flush()
            if blocks and not para and blocks[-1].startswith("- ") and previous is not None \
                    and previous.page == ln.page and 0 < previous.y - ln.y <= ln.size * 1.6 \
                    and not previous.bold and previous.size == ln.size:
                blocks[-1] += " " + ln.text            # a bullet wrapped onto the next line
            else:
                para.append(ln)
        previous = ln
    flush()
    md = "\n\n".join(blocks)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def pdf_markdown(reader, max_pages: int) -> str:
    """Markdown for a pypdf reader ("" when the pages carry no readable text)."""
    pages = []
    for i, page in enumerate(reader.pages[:max_pages]):
        plain = re.sub(r"\s+", " ", page.extract_text() or "")
        pages.append(_lines(i, _spans(page), plain))
    return lines_markdown(pages)

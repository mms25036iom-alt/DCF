"""Portable, safe PDF rendering for a saved equity-research Markdown document."""

from __future__ import annotations

import re
from html import escape
from io import BytesIO

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from research.reporting.live_report import public_source_url, redact_private_locations

INK = colors.HexColor("#19343c")
TEAL = colors.HexColor("#18786b")
MUTED = colors.HexColor("#657972")
LINE = colors.HexColor("#dce5df")
PAPER_TINT = colors.HexColor("#f3f7f3")


def _fonts() -> tuple[str, str, str]:
    """Core PDF fonts avoid platform paths and dynamic font-subset state."""
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _clean(value: str) -> str:
    substitutions = {ord(char): "-" for char in "‐‑‒–—―−"}
    substitutions.update({ord("₹"): "INR ", ord("×"): "x", ord("→"): "->", ord("\u00a0"): " "})
    return re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
        "",
        redact_private_locations(str(value)).translate(substitutions),
    )


def _safe_link(value: str) -> str | None:
    return public_source_url(value)


def _inline(children) -> str:
    """Render only a small Markdown inline whitelist into ReportLab markup."""
    result, links = [], []
    for token in children or []:
        if token.type in {"text", "html_inline"}:
            result.append(escape(_clean(token.content)))
        elif token.type == "strong_open":
            result.append("<b>")
        elif token.type == "strong_close":
            result.append("</b>")
        elif token.type == "em_open":
            result.append("<i>")
        elif token.type == "em_close":
            result.append("</i>")
        elif token.type == "s_open":
            result.append("<strike>")
        elif token.type == "s_close":
            result.append("</strike>")
        elif token.type == "code_inline":
            result.append('<font name="Courier">' + escape(_clean(token.content)) + "</font>")
        elif token.type == "link_open":
            href = _safe_link(token.attrGet("href") or "")
            links.append(bool(href))
            if href:
                result.append(f'<link href="{escape(href, quote=True)}" color="#18786b">')
        elif token.type == "link_close":
            if links and links.pop():
                result.append("</link>")
        elif token.type == "hardbreak":
            result.append("<br/>")
        elif token.type == "softbreak":
            result.append(" ")
        elif token.type == "image":
            result.append(
                escape("[Figure: " + _clean(token.content or "external image omitted") + "]")
            )
        elif token.content:
            result.append(escape(_clean(token.content)))
    return "".join(result)


def _metadata(markdown: str) -> tuple[dict, str]:
    facts, body = {}, []
    cover = True
    known = {
        "company",
        "ticker",
        "data mode",
        "exchange",
        "currency",
        "generated",
        "rating",
        "report date",
        "report version",
        "financial data cutoff",
        "display units",
    }
    for line in markdown.splitlines():
        if line.startswith("## "):
            cover = False
        match = re.match(r"^\*\*([^*]+):\*\*\s*(.*)$", line) if cover else None
        if match and match[1].strip().lower() in known:
            facts[match[1].strip().lower()] = match[2].strip()
        elif cover and re.match(r"^#\s+(?:Astra Research Report|Research Report)\s*$", line, re.I):
            continue
        else:
            body.append(line)
    return facts, "\n".join(body)


def _read_table(tokens, start: int) -> tuple[list[list[tuple[str, str]]], int]:
    rows, row, content, plain = [], [], "", ""
    index = start + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_close":
            return rows, index + 1
        if token.type == "tr_open":
            row = []
        elif token.type in {"th_open", "td_open"}:
            content, plain = "", ""
        elif token.type == "inline":
            content += _inline(token.children)
            plain += token.content
        elif token.type in {"th_close", "td_close"}:
            row.append((content, _clean(plain)))
        elif token.type == "tr_close":
            rows.append(row)
        index += 1
    return rows, index


def _numeric(value: str) -> bool:
    value = value.strip().replace(",", "").rstrip("%")
    if value in {"N/A", "-", ""}:
        return True
    return bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value))


def _report_table(rows, width: float, styles: dict, page_height: float) -> LongTable | Spacer:
    if not rows or not rows[0]:
        return Spacer(1, 1)
    count = max(len(row) for row in rows)
    if count == 1:
        widths = [width]
    elif count == 2:
        widths = [width * 0.36, width * 0.64]
    elif count == 3:
        widths = [width * 0.28, width * 0.19, width * 0.53]
    else:
        first = min(155, width * 0.29)
        widths = [first] + [(width - first) / (count - 1)] * (count - 1)
    numeric = [
        all(_numeric(row[column][1]) for row in rows[1:] if column < len(row))
        for column in range(count)
    ]
    data = []
    for index, row in enumerate(rows):
        cells = []
        for column in range(count):
            value = row[column][0] if column < len(row) else ""
            style = (
                styles["table_header"]
                if index == 0
                else styles["table_number"]
                if column and numeric[column]
                else styles["table"]
            )
            cells.append(Paragraph(value or " ", style))
        data.append(cells)
    table = LongTable(
        data,
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
        rowSplitRange=(3, len(data) - 2) if len(data) > 5 else None,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER_TINT]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    table.spaceBefore, table.spaceAfter = 5, 14
    # Compact schedules should remain one visual unit. Large statement tables
    # retain repeated headers and can split with at least two body rows per part.
    if len(data) <= 13 and table.wrap(width, page_height)[1] <= page_height - 96:
        table._astra_compact = True
    return table


def _append_compact_section(story: list, content: list, width: float, page_height: float) -> None:
    """Keep a short schedule/list together with its nearby heading and introduction."""
    start, found_heading = len(story), False
    for index in range(len(story) - 1, max(-1, len(story) - 4), -1):
        previous = story[index]
        if not isinstance(previous, Paragraph):
            break
        if previous.style.name in {"AstraH2", "AstraH3"}:
            start, found_heading = index, True
            break
        if previous.style.name != "AstraBody":
            break
    if not found_heading:
        start = len(story)
    group = [*story[start:], *content]
    height = sum(
        item.wrap(width, page_height)[1] + item.getSpaceBefore() + item.getSpaceAfter()
        for item in group
    )
    if height <= page_height - 12:
        del story[start:]
        story.append(KeepTogether(group))
    else:
        story.extend(content)


def render_report_pdf(markdown: str) -> bytes:
    """Return a downloadable PDF without executing source HTML or fetching assets."""
    normal, bold, italic = _fonts()
    output = BytesIO()
    facts, body = _metadata(markdown)
    company, ticker = facts.get("company", "Equity research"), facts.get("ticker", "N/A")
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=60,
        bottomMargin=47,
        title=_clean(company + " - Equity research"),
        author="Astra Research",
        pageCompression=1,
    )
    base = getSampleStyleSheet()["BodyText"]
    styles = {
        "body": ParagraphStyle(
            "AstraBody",
            parent=base,
            fontName=normal,
            fontSize=10,
            leading=15,
            textColor=INK,
            spaceAfter=9,
            splitLongWords=1,
            allowWidows=0,
            allowOrphans=0,
        ),
        "title": ParagraphStyle(
            "AstraTitle",
            parent=base,
            fontName=bold,
            fontSize=25,
            leading=31,
            textColor=INK,
            spaceAfter=13,
        ),
        "eyebrow": ParagraphStyle(
            "AstraEyebrow",
            parent=base,
            fontName=bold,
            fontSize=8,
            leading=12,
            textColor=TEAL,
            spaceAfter=9,
            tracking=1,
        ),
        "meta": ParagraphStyle(
            "AstraMeta",
            parent=base,
            fontName=normal,
            fontSize=9,
            leading=13,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "AstraH2",
            parent=base,
            fontName=bold,
            fontSize=15,
            leading=20,
            textColor=INK,
            spaceBefore=18,
            spaceAfter=9,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "AstraH3",
            parent=base,
            fontName=bold,
            fontSize=11.5,
            leading=16,
            textColor=TEAL,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "table": ParagraphStyle(
            "AstraTable",
            parent=base,
            fontName=normal,
            fontSize=8.2,
            leading=11.7,
            textColor=INK,
            splitLongWords=1,
        ),
        "table_number": ParagraphStyle(
            "AstraNumber",
            parent=base,
            fontName=normal,
            fontSize=8.2,
            leading=11.7,
            textColor=INK,
            alignment=TA_RIGHT,
            splitLongWords=1,
        ),
        "table_header": ParagraphStyle(
            "AstraTableHeader",
            parent=base,
            fontName=bold,
            fontSize=8,
            leading=11.5,
            textColor=colors.white,
            splitLongWords=1,
        ),
        "quote": ParagraphStyle(
            "AstraQuote",
            parent=base,
            fontName=italic,
            fontSize=9,
            leading=14,
            textColor=MUTED,
            leftIndent=13,
            rightIndent=8,
            spaceAfter=7,
            splitLongWords=1,
        ),
        "code": ParagraphStyle(
            "AstraCode",
            parent=base,
            fontName="Courier",
            fontSize=8.2,
            leading=11.5,
            textColor=INK,
            backColor=PAPER_TINT,
            borderPadding=8,
            spaceBefore=5,
            spaceAfter=10,
            splitLongWords=1,
        ),
    }
    story = [
        Paragraph("EQUITY RESEARCH MEMORANDUM", styles["eyebrow"]),
        Paragraph(escape(_clean(company)), styles["title"]),
    ]
    date = facts.get("generated") or facts.get("report date") or "Not supplied"
    display_date = str(date)[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", str(date)) else str(date)
    metadata = [
        f"<b>{escape(_clean(ticker))}</b>  |  {escape(_clean(facts.get('exchange', 'Not verified')))}  |  {escape(_clean(display_date))}",
        f"{escape(_clean(facts.get('data mode', 'Unspecified')))} observations  |  {escape(_clean(facts.get('display units', facts.get('currency', 'Unverified currency'))))}  |  {escape(_clean(facts.get('rating', 'NOT RATED')))}",
    ]
    story.extend(Paragraph(value, styles["meta"]) for value in metadata)
    story.extend(
        [Spacer(1, 12), HRFlowable(width="100%", thickness=1.1, color=TEAL, spaceAfter=15)]
    )
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(body)
    index, lists, quote_depth, pending_bullet = 0, [], 0, None
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_open":
            rows, index = _read_table(tokens, index)
            table = _report_table(rows, doc.width, styles, doc.height)
            if getattr(table, "_astra_compact", False):
                _append_compact_section(story, [table], doc.width, doc.height)
            else:
                story.append(table)
            continue
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            lists.append(
                {
                    "ordered": token.type == "ordered_list_open",
                    "next": int(token.attrGet("start") or 1),
                    "story_start": len(story),
                }
            )
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            if lists:
                completed = lists.pop()
                if not lists:
                    start = completed["story_start"]
                    content = story[start:]
                    del story[start:]
                    _append_compact_section(story, content, doc.width, doc.height)
        elif token.type == "list_item_open" and lists:
            current = lists[-1]
            pending_bullet = f"{current['next']}." if current["ordered"] else "•"
            current["next"] += 1
        elif token.type == "list_item_close":
            pending_bullet = None
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token.type == "heading_open" and index + 1 < len(tokens):
            content = _inline(tokens[index + 1].children)
            story.append(
                Paragraph(
                    content or " ", styles["h2"] if token.tag in {"h1", "h2"} else styles["h3"]
                )
            )
            index += 2
        elif token.type == "paragraph_open" and index + 1 < len(tokens):
            content = _inline(tokens[index + 1].children)
            style = styles["quote"] if quote_depth else styles["body"]
            if lists:
                style = ParagraphStyle(
                    f"AstraList{len(lists)}",
                    parent=style,
                    leftIndent=14 * len(lists),
                    bulletIndent=14 * (len(lists) - 1),
                    bulletFontName=normal,
                    bulletFontSize=9,
                    spaceAfter=6,
                )
            paragraph = Paragraph(content or " ", style, bulletText=pending_bullet)
            story.append(paragraph)
            pending_bullet = None
            index += 2
        elif token.type in {"fence", "code_block"}:
            story.append(
                Paragraph(escape(_clean(token.content)).replace("\n", "<br/>"), styles["code"])
            )
        elif token.type == "hr":
            story.append(
                HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=8, spaceAfter=12)
            )
        index += 1

    def page(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(TEAL)
        canvas.setFont(bold, 8)
        canvas.drawString(42, height - 30, "ASTRA RESEARCH")
        canvas.setFillColor(MUTED)
        canvas.setFont(normal, 7.5)
        canvas.drawRightString(width - 42, height - 30, _clean(str(ticker))[:45])
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(42, height - 38, width - 42, height - 38)
        canvas.line(42, 36, width - 42, 36)
        canvas.setFont(normal, 7)
        canvas.drawString(42, 24, "Personal research | NOT RATED | Verify primary sources")
        canvas.drawRightString(width - 42, 24, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page, onLaterPages=page)
    return output.getvalue()

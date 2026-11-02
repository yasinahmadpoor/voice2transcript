"""Styled exports with literal transcript text and bundled licensed fonts."""

import base64
import re
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import uuid4

from common.http import APIError
from .execution import run_isolated

MAX_EXPORT_CHARACTERS = 100_000
EXPORT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_NAME = "Vazirmatn"
ACCENT = "514789"
_export_lock = Lock()
LABEL = re.compile(r"^(?:\[[\d:.]+\]\s*)?[^:\n]{1,70}[:：](?:\s|$)")


def direction(text):
    for char in text:
        bidi = unicodedata.bidirectional(char)
        if bidi in {"R", "AL"}:
            return "rtl"
        if bidi == "L":
            return "ltr"
    return "ltr"


def document_data(text, filename, finished_at):
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    title = stem or "Recording"
    date = datetime.fromtimestamp(finished_at, timezone.utc).strftime(
        "%d %b %Y · %H:%M UTC"
    )
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = LABEL.match(line)
        body = line[match.end() :] if match else line
        lines.append(
            {
                "label": line[: match.end()] if match else "",
                "body": body,
                "direction": direction(body or line),
            }
        )
    return {"title": title, "direction": direction(title), "date": date, "lines": lines}


def export_filename(filename, format):
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = "".join(
        char for char in stem if not unicodedata.category(char).startswith("C")
    )
    stem = stem.strip(" .")[:120] or "recording"
    return f"{stem}-transcript.{format}"


def generate_export(format, text, filename, finished_at):
    """One bounded child per web process; no transcription slot or AI call."""
    if not _export_lock.acquire(blocking=False):
        raise APIError(
            429,
            "Another document is being generated. Try again shortly.",
            {"Retry-After": "5"},
        )
    try:
        encoded = run_isolated(
            {
                "document_export": {
                    "format": format,
                    "text": text,
                    "filename": filename,
                    "finished_at": finished_at,
                }
            },
            timeout_seconds=45,
        )
        return base64.b64decode(encoded, validate=True)
    except TimeoutError:
        raise APIError(
            504,
            "Document generation timed out. Try a shorter transcript or download TXT.",
        ) from None
    except (RuntimeError, ValueError):
        raise APIError(
            503, "Could not generate the document. Try again or download TXT."
        ) from None
    finally:
        _export_lock.release()


def render_export(format, text, filename, finished_at):
    data = document_data(text, filename, finished_at)
    return render_docx(data) if format == "docx" else render_pdf(data)


def render_pdf(data):
    from django.template.loader import render_to_string
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    # Only these fonts can be fetched, never a URL or file from user text.
    resources = {
        f"https://export.invalid/{name}": (FONTS / name).read_bytes()
        for name in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf")
    }

    class BundledFontFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            if url not in resources:
                raise ValueError("External document resources are disabled.")
            return URLFetcherResponse(url, resources[url], {"Content-Type": "font/ttf"})

    html = render_to_string("transcriptions/export.html", data)
    return HTML(
        string=html, url_fetcher=BundledFontFetcher(fail_on_errors=True)
    ).write_pdf()


def render_docx(data):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    document = Document()
    document.core_properties.title = data["title"]
    document.core_properties.author = "Voice to Transcript"
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(22)
    section.left_margin = section.right_margin = Mm(23)
    section.header_distance = section.footer_distance = Mm(10)
    for name, size, color in (
        ("Normal", 11, "202631"),
        ("Title", 23, "000000"),
        ("Subtitle", 10, "626B7A"),
    ):
        style = document.styles[name]
        style.font.name = FONT_NAME
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.font.italic = False
        style.font.bold = name == "Title"
        style.font.cs_bold = name == "Title"
        style.font.cs_italic = False
        style.font.underline = False
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        for attr in list(fonts.attrib):
            if "theme" in attr.lower():
                del fonts.attrib[attr]
        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn(f"w:{attr}"), FONT_NAME)
        style.paragraph_format.space_after = Pt(8)
        style.paragraph_format.line_spacing = 1.45
        style.paragraph_format.widow_control = True
        border = style.element.get_or_add_pPr().find(qn("w:pBdr"))
        if border is not None:
            border.getparent().remove(border)
        props = style.element.get_or_add_rPr()
        for tag in ("w:spacing", "w:kern"):
            previous = props.find(qn(tag))
            if previous is not None:
                props.remove(previous)
        previous = props.find(qn("w:szCs"))
        if previous is not None:
            props.remove(previous)
        cs_size = OxmlElement("w:szCs")
        cs_size.set(qn("w:val"), str(size * 2))
        props.append(cs_size)

    def set_direction(paragraph, rtl):
        paragraph.alignment = (
            WD_ALIGN_PARAGRAPH.RIGHT if rtl else WD_ALIGN_PARAGRAPH.LEFT
        )
        bidi = OxmlElement("w:bidi")
        bidi.set(qn("w:val"), "1" if rtl else "0")
        paragraph._p.get_or_add_pPr().append(bidi)

    def add_run(paragraph, text, bold=False):
        run = paragraph.add_run(text)
        run.bold = bold
        run.font.cs_bold = bold
        if direction(text) == "rtl":
            run.font.rtl = True
        if bold:
            run.font.color.rgb = RGBColor.from_string(ACCENT)

    header = section.header.paragraphs[0]
    header.text = "VOICE TO TRANSCRIPT"
    header.runs[0].font.size = Pt(8)
    header.runs[0].font.color.rgb = RGBColor.from_string(ACCENT)
    title = document.add_paragraph(data["title"], "Title")
    set_direction(title, data["direction"] == "rtl")
    title.paragraph_format.keep_with_next = True
    subtitle = document.add_paragraph(f"Transcript · {data['date']}", "Subtitle")
    subtitle.paragraph_format.space_after = Pt(20)
    subtitle.paragraph_format.keep_with_next = True
    for line in data["lines"]:
        paragraph = document.add_paragraph()
        set_direction(paragraph, line["direction"] == "rtl")
        if line["label"]:
            add_run(paragraph, line["label"], bold=True)
        add_run(paragraph, line["body"])
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("Page ").font.size = Pt(9)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    _embed_fonts(document)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _embed_fonts(document):
    """Embed regular/bold fonts with OOXML's GUID XOR obfuscation."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.oxml import parse_xml
    from docx.opc.oxml import serialize_part_xml

    table = document.part.part_related_by(RT.FONT_TABLE)
    root = parse_xml(table.blob)
    font = OxmlElement("w:font")
    font.set(qn("w:name"), FONT_NAME)
    for kind, name in (
        ("Regular", "Vazirmatn-Regular.ttf"),
        ("Bold", "Vazirmatn-Bold.ttf"),
    ):
        key = uuid4()
        payload = bytearray((FONTS / name).read_bytes())
        mask = key.bytes[::-1]
        for index in range(32):
            payload[index] ^= mask[index % 16]
        part = Part(
            PackURI(f"/word/fonts/{kind.lower()}.odttf"),
            "application/vnd.openxmlformats-officedocument.obfuscatedFont",
            bytes(payload),
            table.package,
        )
        relation = table.relate_to(part, RT.FONT)
        embed = OxmlElement(f"w:embed{kind}")
        embed.set(qn("r:id"), relation)
        embed.set(qn("w:fontKey"), "{" + str(key).upper() + "}")
        embed.set(qn("w:subsetted"), "0")
        font.append(embed)
    root.append(font)
    table._blob = serialize_part_xml(root)
    document.settings.element.append(OxmlElement("w:embedTrueTypeFonts"))

"""Word-Dokument (.docx) ohne Fremdbibliothek (0.71.0, Export fuer Confluence).

Der Anwender importiert die Doku von Hand in Confluence („Word-Dokument importieren“) und
verbessert sie vorher in Word - Automato schreibt nichts nach Confluence. Darum nur, was der
Import sauber uebernimmt: Titel, Ueberschriften 1-3 (mit Gliederungsebene, Confluence macht
daraus die Struktur der Seite), Absaetze mit fett/kursiv, Aufzaehlungen, Tabellen mit
Kopfzeile (wiederholt auf jeder Seite) und Hinweise. Querformat A4, damit breite Tabellen
passen. Minimales WordprocessingML: `[Content_Types].xml`, Beziehungen, `document.xml`,
`styles.xml`, Dokumenteigenschaften - von Word, LibreOffice und dem Confluence-Import gelesen.
"""

import io
import re
import zipfile
from datetime import UTC, datetime
from xml.sax.saxutils import escape

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Zeichen, die XML 1.0 nicht erlaubt (Steuerzeichen aus Beschreibungen der Geraete)
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
# Querformat A4 in Twips, Rand 1,5 cm
PAGE_W, PAGE_H, MARGIN = 16838, 11906, 850
TEXT_W = PAGE_W - 2 * MARGIN


def _text(value) -> str:
    return escape(_BAD.sub("", "" if value is None else str(value)))


def _run(text: str, bold: bool = False, italic: bool = False, size: int | None = None,
         color: str | None = None) -> str:  # fmt: skip
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    parts = str(text).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{_text(p)}</w:t>' for p in parts)
    return f"<w:r>{rpr}{body}</w:r>"


class Document:
    """Ein Dokument aus Bloecken; `bytes()` liefert die .docx-Datei."""

    def __init__(self, title: str, subject: str | None = None):
        self.title = title
        self.subject = subject
        self._body: list[str] = []

    # -- Bloecke ----------------------------------------------------------------------------

    def heading(self, text: str, level: int = 1) -> None:
        style = "Title" if level == 0 else f"Heading{min(max(level, 1), 3)}"
        self._body.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{_run(text)}</w:p>')

    def paragraph(self, *parts, style: str | None = None) -> None:
        """Absatz aus Text bzw. (Text, {"bold"/"italic": True}) - Teile ohne Leerzeichen
        dazwischen."""
        runs = []
        for part in parts:
            if isinstance(part, tuple):
                text, fmt = part
                runs.append(_run(text, bold=fmt.get("bold", False), italic=fmt.get("italic", False),
                                 color=fmt.get("color")))  # fmt: skip
            elif part:
                runs.append(_run(part))
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        self._body.append(f"<w:p>{ppr}{''.join(runs)}</w:p>")

    def note(self, text: str) -> None:
        """Hinweis (klein, grau, kursiv)."""
        self.paragraph((text, {"italic": True, "color": "666666"}), style="Note")

    def bullets(self, items: list[str]) -> None:
        for item in items:
            self.paragraph(f"• {item}", style="ListBullet")

    def fields(self, rows: list[tuple[str, str]]) -> None:
        """Zweispaltige Tabelle Feld/Wert ohne Kopfzeile (Steckbrief)."""
        rows = [(k, v) for k, v in rows if v not in (None, "")]
        if rows:
            self.table(["", ""], [[k, v] for k, v in rows], widths=[3, 9], head=False)

    def table(self, columns: list[str], rows: list[list], widths: list[float] | None = None,
              head: bool = True, head_fill: str = "E8EEF6") -> None:  # fmt: skip
        """Tabelle ueber die ganze Breite; `widths` als Verhaeltnis je Spalte, `head` =
        Kopfzeile (wiederholt sich auf jeder Seite)."""
        cols = max(len(columns), max((len(r) for r in rows), default=0)) or 1
        rel = list(widths or [1] * cols)[:cols] + [1] * max(0, cols - len(widths or []))
        total = sum(rel) or 1
        tw = [int(TEXT_W * w / total) for w in rel]
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in tw)
        out = [
            "<w:tbl><w:tblPr>"
            '<w:tblStyle w:val="Table"/>'
            f'<w:tblW w:w="{TEXT_W}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            "</w:tblPr>"
            f"<w:tblGrid>{grid}</w:tblGrid>"
        ]

        def cell(value, width: int, bold: bool = False, fill: str | None = None) -> str:
            shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
            return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
                    f'<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr>'
                    f"{_run('' if value is None else value, bold=bold)}</w:p></w:tc>")  # fmt: skip

        if head:
            out.append("<w:tr><w:trPr><w:tblHeader/></w:trPr>")
            out += [cell(h, tw[i], True, head_fill) for i, h in enumerate(columns[:cols])]
            out += [cell("", tw[i], True, head_fill) for i in range(len(columns), cols)]
            out.append("</w:tr>")
        for r in rows:
            out.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>")
            values = list(r) + [""] * (cols - len(r))
            out += [cell(v, tw[i], bold=not head and i == 0) for i, v in enumerate(values)]
            out.append("</w:tr>")
        out.append("</w:tbl>")
        self._body.append("".join(out))
        self._body.append('<w:p><w:pPr><w:pStyle w:val="Spacer"/></w:pPr></w:p>')

    # -- Datei ------------------------------------------------------------------------------

    def bytes(self, when: datetime | None = None) -> bytes:
        when = when or datetime.now(UTC)
        sect = (f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}" w:orient="landscape"/>'
                f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" '
                f'w:left="{MARGIN}" w:header="425" w:footer="425" w:gutter="0"/>'
                '</w:sectPr>')  # fmt: skip
        document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>'
                    f"{''.join(self._body)}{sect}</w:body></w:document>")  # fmt: skip
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        core = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_text(self.title)}</dc:title>"
            f"<dc:subject>{_text(self.subject or '')}</dc:subject>"
            "<dc:creator>Automato</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
            "</cp:coreProperties>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _CONTENT_TYPES)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
            z.writestr("word/document.xml", document)
            z.writestr("word/styles.xml", _STYLES)
            z.writestr("docProps/core.xml", core)
            z.writestr("docProps/app.xml", _APP)
        return buf.getvalue()


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
    'package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.extended-properties+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'
)
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/styles" Target="styles.xml"/></Relationships>'
)
_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-'
    'properties"><Application>Automato</Application></Properties>'
)


def _heading_style(sid: str, name: str, level: int, size: int, before: int) -> str:
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/>'
            '<w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>'
            f'<w:spacing w:before="{before}" w:after="80"/><w:outlineLvl w:val="{level}"/>'
            f'</w:pPr><w:rPr><w:b/><w:color w:val="1F3A5F"/><w:sz w:val="{size}"/>'
            f'<w:szCs w:val="{size}"/></w:rPr></w:style>')  # fmt: skip


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{_W}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="de-CH"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="259" w:lineRule="auto"/></w:pPr>'
    "</w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "<w:qFormat/></w:style>"
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:after="120"/></w:pPr>'
    '<w:rPr><w:b/><w:color w:val="1F3A5F"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr>'
    "</w:style>"
    + _heading_style("Heading1", "heading 1", 0, 30, 280)
    + _heading_style("Heading2", "heading 2", 1, 25, 220)
    + _heading_style("Heading3", "heading 3", 2, 22, 160)
    + '<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/>'
    '<w:basedOn w:val="Normal"/><w:rPr><w:i/><w:color w:val="666666"/><w:sz w:val="17"/>'
    '<w:szCs w:val="17"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="40"/><w:ind w:left="360"/></w:pPr>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="20" w:after="20" w:line="240" '
    'w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="17"/><w:szCs w:val="17"/></w:rPr>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Spacer"><w:name w:val="Spacer"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/></w:pPr>'
    '<w:rPr><w:sz w:val="8"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="Table"><w:name w:val="Automato Table"/>'
    "<w:tblPr><w:tblBorders>"
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="B7C3D4"/>'
    '</w:tblBorders><w:tblCellMar><w:left w:w="70" w:type="dxa"/>'
    '<w:right w:w="70" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
    "</w:styles>"
)

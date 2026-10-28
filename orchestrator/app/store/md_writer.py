"""Markdown mit derselben Schnittstelle wie `docx_writer.Document` (0.104.0, Modularisierung).

Bisher baute die Oberflaeche den Markdown-Export selbst aus den Daten der Doku - jede Aenderung
an der Doku musste an zwei Stellen nachgezogen werden (Word im Server, Markdown im Browser).
Jetzt schreibt `word_export` beide aus demselben Aufbau: Abschnitte, Tabellen und Hinweise
gehen an einen Schreiber - `docx_writer.Document` fuer Word, `Markdown` fuer Markdown
(GitHub-Tabellen; Zeilenumbrueche in Zellen als `<br>`).
"""

from datetime import datetime


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _inline(value) -> str:
    return ("" if value is None else str(value)).replace("\r", "").replace("\n", " ")


class Markdown:
    """Ein Dokument aus Bloecken; `bytes()` liefert den Text (UTF-8)."""

    def __init__(self, title: str, subject: str | None = None):
        self.title = title
        self.subject = subject
        self._lines: list[str] = []

    def _block(self, *lines: str) -> None:
        self._lines += [*lines, ""]

    def heading(self, text: str, level: int = 1) -> None:
        self._block(f"{'#' * (min(max(level, 0), 3) + 1)} {_inline(text)}")

    def paragraph(self, *parts, style: str | None = None) -> None:
        """Absatz aus Text bzw. (Text, {"bold"/"italic": True})."""
        out = []
        for part in parts:
            if isinstance(part, tuple):
                text, fmt = part
                text, core = _inline(text), _inline(text).strip()
                mark = "**" if fmt.get("bold") else "_" if fmt.get("italic") else ""
                if core and mark:  # Leerzeichen bleiben ausserhalb der Auszeichnung
                    i = text.index(core)
                    text = f"{text[:i]}{mark}{core}{mark}{text[i + len(core):]}"
                out.append(text)
            elif part:
                out.append(_inline(part))
        if out:
            self._block("".join(out))

    def note(self, text: str) -> None:
        self.paragraph((text, {"italic": True}))

    def bullets(self, items: list[str]) -> None:
        if items:
            self._block(*(f"- {_inline(x)}" for x in items))

    def fields(self, rows: list[tuple[str, str]]) -> None:
        """Steckbrief: eine Zeile je Feld mit Wert."""
        rows = [(k, v) for k, v in rows if v not in (None, "")]
        if rows:
            self._block(*(f"- **{_inline(k)}:** {_inline(v)}" for k, v in rows))

    def table(self, columns: list[str], rows: list[list], widths: list[float] | None = None,
              head: bool = True, head_fill: str = "") -> None:  # fmt: skip
        """Tabelle; `widths` und `head_fill` gelten nur fuer Word."""
        cols = max(len(columns), max((len(r) for r in rows), default=0)) or 1
        heads = [*columns, *[""] * (cols - len(columns))] if head else [""] * cols
        lines = ["| " + " | ".join(_cell(h) for h in heads) + " |",
                 "| " + " | ".join("---" for _ in heads) + " |"]  # fmt: skip
        for r in rows:
            values = [*r, *[""] * (cols - len(r))]
            lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
        self._block(*lines)

    def bytes(self, when: datetime | None = None) -> bytes:
        """Text (UTF-8); `when` wie bei Word (Dokumenteigenschaften) - Markdown hat keine."""
        del when
        return "\n".join(self._lines).rstrip("\n").encode() + b"\n"

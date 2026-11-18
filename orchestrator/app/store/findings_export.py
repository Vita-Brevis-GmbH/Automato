"""Findings als Excel, nach Kategorie (1.0.1, Anwender: „globaler Export aller Findings,
kategorisiert … Excel, einfach mit etwas Details als Kommentar, damit man später noch weiss, was
war“).

Kategorie = Modul (ADR 0036) → Prüfung (ADR 0039) → Typ. Blatt „Übersicht“ mit den Zählern je
Status, danach ein Blatt je Modul mit allen Findings (alle Status). Die Details des Befunds (Text,
Hinweis, gelesene Angaben) stehen als Kommentar an der Meldung - so bleibt nachvollziehbar, was
zum Zeitpunkt des Exports war. Nur lesend.
"""

import io
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app import __version__, checks, modules
from app.i18n import L
from app.models import Finding, FindingStatus, Severity
from app.store import finding_text

COMMENT_MAX = 2000  # Zeichen je Kommentar (Excel erlaubt mehr, lesbar bleibt es so)
_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
_HEAD = Font(bold=True)
_FILL = PatternFill("solid", fgColor="DDE4EE")


@dataclass(frozen=True)
class Category:
    order: tuple
    module: str  # Titel des Moduls
    check: str  # Titel der Prüfung


def categories() -> dict[str, Category]:
    """Kategorie je Befund-Typ aus den Prüfungen; Typen ohne Prüfung (z. B. von Hand erfasst)
    fallen unter „Weitere“."""
    out: dict[str, Category] = {}
    mods = {m.key: (i, m) for i, m in enumerate(modules.MODULES)}
    for c in checks.all_checks():
        idx, mod = mods.get(c.module, (len(mods), None))
        title = L(*mod.title) if mod else c.module
        for t in c.types:
            out.setdefault(t.value, Category((idx, c.order, c.key), title, L(*c.title)))
    return out


def _other(ftype: str) -> Category:
    if ftype == "manual":
        return Category((999, 0, ftype), L("Weitere", "Other"), L("Von Hand erfasst", "Manual"))
    return Category((999, 1, ftype), L("Weitere", "Other"), ftype)


def _text(value) -> str | int | None:
    if value is None or isinstance(value, int):
        return value
    s = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    return s[:32000]


def _put(ws, row: int, col: int, value):
    cell = ws.cell(row=row, column=col, value=_text(value))
    if isinstance(cell.value, str):
        cell.data_type = "s"  # nie als Formel (Werte aus Geräten, z. B. „=…“)
    return cell


def _when(value: datetime | None) -> str | None:
    return f"{value:%Y-%m-%d %H:%M}" if value else None


def _comment(message: str, hint: str, detail: dict | None, note: str | None) -> str:
    parts = [message]
    if hint:
        parts.append(f"{L('Hinweis', 'Hint')}: {hint}")
    if note:
        parts.append(f"{L('Notiz', 'Note')}: {note}")
    if detail:
        dump = json.dumps(detail, ensure_ascii=False, indent=1, default=str)
        parts.append(f"{L('Details', 'Details')}:\n{dump}")
    text = "\n\n".join(p for p in parts if p)
    if len(text) > COMMENT_MAX:
        text = text[: COMMENT_MAX - 1] + "…"
    return ILLEGAL_CHARACTERS_RE.sub("", text)


def _sheet_name(title: str, used: set[str]) -> str:
    base = "".join(ch for ch in title if ch not in "[]:*?/\\")[:28] or "Findings"
    name, n = base, 2
    while name.lower() in used:
        name, n = f"{base[:26]} {n}", n + 1
    used.add(name.lower())
    return name


def _header(ws, labels: list[str], widths: list[int]) -> None:
    for i, (label, width) in enumerate(zip(labels, widths, strict=True), start=1):
        cell = _put(ws, 1, i, label)
        cell.font, cell.fill = _HEAD, _FILL
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"


def workbook(findings: Iterable[Finding], tenants: dict[int, str], sources: dict[int, str],
             when: datetime, detail: Callable[[dict | None], dict | None] = lambda d: d,
             scope: str | None = None,
             clean: Callable[[object], object] = lambda v: v) -> bytes:  # fmt: skip
    """Excel-Datei (.xlsx) der Findings, nach Kategorie. `detail` filtert die Angaben und `clean`
    ersetzt Gerätenamen (Kunden-Benutzer, ADR 0018 - die Middleware sieht nur JSON),
    `tenants`/`sources` die Namen (leer = ohne Namen)."""
    cats = categories()
    rows = []
    for f in findings:
        cat = cats.get(f.type.value) or _other(f.type.value)
        d = detail(f.detail)
        message, hint = finding_text.explain(f.type.value, f.subject_id, d)
        texts = clean({"subject": f.subject_id, "message": message, "hint": hint,
                       "note": f.note, "detail": d})  # fmt: skip
        rows.append((cat, f, texts))
    rows.sort(key=lambda r: (r[0].order, tenants.get(r[1].tenant_id, ""),
                             _SEVERITY_ORDER.get(r[1].severity, 9), r[2]["subject"] or "",
                             r[1].id))  # fmt: skip

    wb = Workbook()
    summary = wb.active
    summary.title = _sheet_name(L("Übersicht", "Summary"), used := set())
    statuses = list(FindingStatus)
    status_label = {
        FindingStatus.OPEN: L("offen", "open"),
        FindingStatus.ACKNOWLEDGED: L("quittiert", "acknowledged"),
        FindingStatus.RESOLVED: L("erledigt", "resolved"),
        FindingStatus.IGNORED: L("ignoriert", "ignored"),
    }
    severity_label = {Severity.ERROR: L("Fehler", "Error"),
                      Severity.WARNING: L("Warnung", "Warning"),
                      Severity.INFO: L("Info", "Info")}  # fmt: skip

    # Übersicht: Kategorie -> Zähler je Status
    counts: dict[tuple, dict] = {}
    for cat, f, *_ in rows:
        key = (cat.order, cat.module, cat.check, f.type.value)
        entry = counts.setdefault(key, {s: 0 for s in statuses})
        entry[f.status] += 1
    _header(summary, [L("Modul", "Module"), L("Prüfung", "Check"), L("Typ", "Type"),
                      *(status_label[s] for s in statuses), L("Gesamt", "Total"),
                      L("Blatt", "Sheet")],
            [24, 40, 30, *([12] * len(statuses)), 10, 24])  # fmt: skip
    sheets: dict[str, str] = {}
    for c in dict.fromkeys(cat.module for cat, *_ in rows):
        sheets[c] = _sheet_name(c, used)
    r = 2
    for (_o, module, check, ftype), entry in sorted(counts.items()):
        values = [module, check, ftype, *(entry[s] for s in statuses), sum(entry.values()),
                  sheets[module]]  # fmt: skip
        for i, v in enumerate(values, start=1):
            _put(summary, r, i, v)
        r += 1
    r += 1
    stamp = L(
        f"Stand {when:%d.%m.%Y %H:%M} UTC · Automato {__version__.split('+')[0]}",
        f"As of {when:%d/%m/%Y %H:%M} UTC · Automato {__version__.split('+')[0]}",
    )
    _put(summary, r, 1, stamp).font = Font(italic=True)
    if scope:
        _put(summary, r + 1, 1, scope).font = Font(italic=True)
    _put(summary, r + 2, 1, L(f"{len(rows)} Findings – Details als Kommentar an der Meldung",
                              f"{len(rows)} findings – details as a comment on the message")
         ).font = Font(italic=True)  # fmt: skip

    labels = [L("Mandant", "Tenant"), L("Prüfung", "Check"), L("Typ", "Type"),
              L("Schweregrad", "Severity"), L("Status", "Status"), L("Objekt", "Subject"),
              L("Quellsystem", "Source system"), L("Meldung", "Message"),
              L("Hinweis", "Hint"), L("Notiz", "Note"), L("Erstmals", "First seen"),
              L("Zuletzt", "Last seen"), "ID"]  # fmt: skip
    widths = [16, 34, 26, 11, 12, 40, 20, 70, 50, 30, 17, 17, 9]
    current = {}
    for module, title in sheets.items():
        ws = wb.create_sheet(title)
        _header(ws, labels, widths)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(labels))}1"
        current[module] = [ws, 2]
    wrap = Alignment(wrap_text=False, vertical="top")
    for cat, f, t in rows:
        ws, r = current[cat.module]
        values = [tenants.get(f.tenant_id), cat.check, f.type.value,
                  severity_label.get(f.severity, f.severity.value),
                  status_label.get(f.status, f.status.value), t["subject"],
                  sources.get(f.source_system_id) if f.source_system_id else None,
                  t["message"], t["hint"] or None, t["note"], _when(f.first_seen),
                  _when(f.last_seen), f.id]  # fmt: skip
        for i, v in enumerate(values, start=1):
            cell = _put(ws, r, i, v)
            cell.alignment = wrap
        msg = ws.cell(row=r, column=8)
        msg.comment = Comment(_comment(t["message"], t["hint"], t["detail"], t["note"]),
                              "Automato", width=420, height=260)  # fmt: skip
        current[cat.module][1] = r + 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

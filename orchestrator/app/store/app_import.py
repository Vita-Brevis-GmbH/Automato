"""Stammdaten von Applikationen aus CSV (0.75.0, Entscheid des Anwenders 2026-10-26).

Ausnahme vom Grundsatz „kein CSV-Import“: nur die Stammdaten der Applikationen (Name, externer
und interner Name, Zweck, Kuerzel, Verantwortlich, Kritikalitaet, Standort, Beschreibung) -
Angaben ohne Funktionsrelevanz. Die Mitglieder (APs, EPGs, Server, Regeln) werden danach in der
Oberflaeche zugeordnet; Woerterbuecher bleiben ohne CSV.

- Kopfzeile Pflicht, Spalten in beliebiger Reihenfolge, deutsch oder englisch (`COLUMNS`);
  Trennzeichen `;`, `,` oder Tabulator (erkannt); die Oberflaeche liest die Datei als UTF-8
  bzw. Windows-1252 (Excel) und schickt den Text.
- Je Zeile: neue Applikation (`create`), bestehende gleichen Namens mit geaenderten Feldern
  (`update` - nur ausgefuellte Zellen ueberschreiben, leere lassen den Wert), `unchanged` bzw.
  `error` (Grund je Zeile; die Zeile wird nicht uebernommen, die anderen schon).
- `plan` ist die Vorschau ohne Schreiben, `apply` uebernimmt die Zeilen ohne Fehler.
"""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Site
from app.store import applications as store

MAX_BYTES = 2_000_000
MAX_ROWS = 5000

# Feld -> erkannte Kopfzeilen (klein, Umlaute ausgeschrieben, ohne Leer-/Sonderzeichen)
COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("name", "applikation", "application", "app"),
    "external_name": ("externername", "extern", "externalname", "anzeigename"),
    "internal_name": ("internername", "intern", "internalname"),
    "purpose": ("zweck", "verwendungszweck", "purpose", "umgebung", "stage"),
    "code": ("kuerzel", "code", "projektnummer", "projekt", "projectnumber"),
    "owner": ("verantwortlich", "verantwortlicher", "owner", "besitzer", "zustaendig"),
    "criticality": ("kritikalitaet", "criticality", "kritisch"),
    "site": ("standort", "site"),
    "description": ("beschreibung", "description", "bemerkung", "kommentar"),
}
FIELDS = tuple(COLUMNS)
LIMITS = {"name": 200, "external_name": 200, "internal_name": 200, "code": 50, "owner": 200}
PURPOSE = {
    "dev": "dev", "entwicklung": "dev", "development": "dev",
    "test": "test", "testing": "test", "abnahme": "test", "int": "test",
    "prod": "prod", "produktion": "prod", "production": "prod", "produktiv": "prod",
}  # fmt: skip
CRITICALITY = {
    "low": "low", "niedrig": "low", "tief": "low", "gering": "low",
    "medium": "medium", "mittel": "medium", "normal": "medium",
    "high": "high", "hoch": "high",
    "critical": "critical", "kritisch": "critical", "sehrhoch": "critical",
}  # fmt: skip
TEMPLATE = (
    "Name;Externer Name;Interner Name;Zweck;Kürzel;Verantwortlich;Kritikalität;Standort;"
    "Beschreibung\r\n"
    "Webshop;Webshop Kunden;webshop-prod;prod;042;Team Web;hoch;;Bestellungen und Kasse\r\n"
)


def _key(text: str) -> str:
    """Kopfzeile bzw. Wert vergleichbar: klein, Umlaute ausgeschrieben, nur Buchstaben/Ziffern."""
    t = text.strip().casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t)


_HEADER = {alias: f for f, aliases in COLUMNS.items() for alias in aliases}


@dataclass
class Row:
    line: int
    values: dict[str, str]
    action: str = "create"  # create | update | unchanged | error
    errors: list[str] = field(default_factory=list)
    changes: dict[str, list] = field(default_factory=dict)  # Feld -> [alt, neu]
    application_id: int | None = None
    resolved: dict = field(default_factory=dict)  # Feld -> Wert fuer die Applikation


@dataclass
class Plan:
    columns: dict[str, str]  # Kopfzeile -> Feld
    ignored: list[str]
    rows: list[Row]

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(("create", "update", "unchanged", "error"), 0)
        for r in self.rows:
            out[r.action] += 1
        return out

    def out(self) -> dict:
        return {
            "columns": self.columns,
            "ignored": self.ignored,
            "counts": self.counts(),
            "rows": [
                {
                    "line": r.line,
                    "name": r.values.get("name") or None,
                    "action": r.action,
                    "errors": r.errors,
                    "changes": r.changes,
                    "application_id": r.application_id,
                }
                for r in self.rows
            ],
        }


def _read(text: str) -> tuple[dict[str, str], list[str], list[tuple[int, dict[str, str]]]]:
    text = text.lstrip("\ufeff")
    sample = text[:4096]
    first = sample.splitlines()[0] if sample.strip() else ""
    delim = max((";", ",", "\t"), key=first.count) if first else ";"
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("leere Datei") from None
    columns: dict[str, str] = {}
    ignored: list[str] = []
    index: dict[int, str] = {}
    for i, h in enumerate(header):
        f = _HEADER.get(_key(h))
        if f is None or f in index.values():
            if h.strip():
                ignored.append(h.strip())
            continue
        index[i] = f
        columns[h.strip()] = f
    if "name" not in index.values():
        raise ValueError("Spalte „Name“ fehlt in der Kopfzeile")
    rows: list[tuple[int, dict[str, str]]] = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"zu viele Zeilen (höchstens {MAX_ROWS})")
        values = {f: (cells[i].strip() if i < len(cells) else "") for i, f in index.items()}
        rows.append((reader.line_num, values))
    return columns, ignored, rows


def _sites(session: Session, tenant_id: int) -> dict[str, Site]:
    out: dict[str, Site] = {}
    for s in session.scalars(select(Site).where(Site.tenant_id == tenant_id)):
        out.setdefault(_key(s.name), s)
        out[_key(s.code)] = s  # Code vor Name
    return out


def plan(session: Session, tenant_id: int, text: str) -> Plan:
    """Vorschau: was der Import je Zeile tun wuerde (nichts wird geschrieben)."""
    columns, ignored, raw = _read(text)
    existing = {
        a.name.casefold(): a
        for a in session.scalars(select(Application).where(Application.tenant_id == tenant_id))
    }
    sites = _sites(session, tenant_id)
    codes = {s.id: s.code for s in sites.values()}
    seen: set[str] = set()
    rows: list[Row] = []
    for line, values in raw:
        row = Row(line, values)
        rows.append(row)
        name = values.get("name", "")
        if not name:
            row.errors.append("Name fehlt")
        elif name.casefold() in seen:
            row.errors.append("Name kommt in der Datei mehrfach vor")
        seen.add(name.casefold())
        for f, limit in LIMITS.items():
            if len(values.get(f, "")) > limit:
                row.errors.append(f"{f}: höchstens {limit} Zeichen")
        resolved: dict = {}
        for f in ("external_name", "internal_name", "code", "owner", "description"):
            if values.get(f):
                resolved[f] = values[f]
        if values.get("purpose"):
            p = PURPOSE.get(_key(values["purpose"]))
            if p is None:
                row.errors.append(f"Zweck „{values['purpose']}“ unbekannt (dev, test, prod)")
            resolved["purpose"] = p
        if values.get("criticality"):
            c = CRITICALITY.get(_key(values["criticality"]))
            if c is None:
                row.errors.append(
                    f"Kritikalität „{values['criticality']}“ unbekannt (niedrig, mittel, hoch, "
                    "kritisch)"
                )
            resolved["criticality"] = c
        if values.get("site"):
            site = sites.get(_key(values["site"]))
            if site is None:
                row.errors.append(f"Standort „{values['site']}“ unbekannt")
            resolved["site_id"] = site.id if site else None
        row.resolved = resolved
        if row.errors:
            row.action = "error"
            continue
        app = existing.get(name.casefold())
        before = {f: getattr(app, f) if app is not None else None for f in resolved}
        row.changes = {
            ("site" if f == "site_id" else f): [_shown(f, before[f], codes), _shown(f, v, codes)]
            for f, v in resolved.items()
            if before[f] != v
        }
        if app is not None:
            row.application_id = app.id
            row.action = "update" if row.changes else "unchanged"
    return Plan(columns, ignored, rows)


def _shown(f: str, value, codes: dict[int, str]):
    """Wert fuer die Vorschau: Standort als Code statt Kennung."""
    return codes.get(value) if f == "site_id" and value is not None else value


def apply(session: Session, tenant_id: int, text: str) -> tuple[Plan, list[int]]:
    """Zeilen ohne Fehler uebernehmen; (Vorschau, IDs der angelegten bzw. geaenderten)."""
    result = plan(session, tenant_id, text)
    touched: list[int] = []
    for row in result.rows:
        if row.action == "create":
            app = Application(tenant_id=tenant_id, name=row.values["name"], **row.resolved)
            session.add(app)
            session.flush()
            row.application_id = app.id
            touched.append(app.id)
        elif row.action == "update":
            app = session.get(Application, row.application_id)
            for f, value in row.resolved.items():
                setattr(app, f, value)
            store.touch(app)
            touched.append(app.id)
    session.flush()
    return result, touched

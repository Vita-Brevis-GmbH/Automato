"""Export fuer Confluence (0.71.0, ADR 0026): Applikations-Doku und Zonenplan als Word-Datei
(.docx) plus draw.io-Datei des Plans.

Entscheid des Anwenders: kein Schreibweg nach Confluence - er bekommt Word und draw.io,
verbessert die Doku dort und importiert sie von Hand („Word-Dokument importieren“, draw.io
ueber die draw.io-App in Confluence). Inhalt wie die Ansicht bzw. der Markdown-Export:

- **Applikation:** Steckbrief, Systeme, EPGs, Regeln & Contracts (ohne routingtechnisch
  unmoegliche Treffer), Abhaengigkeiten, Verbindungen Ende zu Ende, Hinweis auf den
  Netzwerkplan (draw.io), die Kategorien 1-10, ausgeblendete Regeln mit Begruendung, Befunde.
- **Zonenplan:** Zonen mit Knoten und Netzen, Uebergaenge mit Netzen, Zonen an den Knoten,
  Zonenmatrix als Tabelle (Absicht, erlaubend/sperrend, gegen die Absicht), Absichten mit
  Diensten und Notiz, Netze ohne Zone - der Plan selbst als draw.io.

`bundle` packt Word und draw.io in ein ZIP (ein Download). Rein lesend.
"""

import io
import re
import zipfile
from datetime import datetime

from app import __version__
from app.store import plan_export
from app.store.docx_writer import Document

PURPOSE = {"dev": "Dev", "test": "Test", "prod": "Prod"}
CRIT = {"low": "niedrig", "medium": "mittel", "high": "hoch", "critical": "kritisch"}
SCOPE = {"assigned": "zugeordnet", "specific": "spezifisch", "subnet": "Subnetz", "any": "any",
         "vzany": "vzAny", "pref_group": "Preferred Group"}  # fmt: skip
RANK = {"assigned": -1, "specific": 0, "subnet": 1, "vzany": 2, "pref_group": 3, "any": 4}
DIRECTION = {"assigned": "zugeordnet", "in": "eingehend", "out": "ausgehend"}
VERDICT = {"allow": "erlaubt", "deny": "gesperrt", "implicit_deny": "implizit gesperrt",
           "depends": "abhängig", "unknown": "unbekannt"}  # fmt: skip
SEVERITY = {"error": "Fehler", "warning": "Warnung", "info": "Hinweis"}
INTENT = {"allow": "erlaubt", "limited": "eingeschränkt", "deny": "gesperrt"}
ZONE_KIND = {"dc": "Datacenter", "edge": "Edge", "transfer": "Transfer", "core": "Core",
             "distribution": "Distribution", "access": "Access", "external": "Public / extern",
             "internal": "intern", "dmz": "DMZ", "management": "Management",
             "shared": "geteilt"}  # fmt: skip
MAX_ROWS = 2000  # je Tabelle; der Rest steht als Hinweis da (Word bleibt bedienbar)


_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                         "ß": "ss"})  # fmt: skip


def slug(name: str, fallback: str) -> str:
    """Dateiname: Umlaute ausgeschrieben (0.73.0), sonst nur Buchstaben, Ziffern, `_`, `-`."""
    plain = (name or "").translate(_UMLAUT)
    return re.sub(r"[^A-Za-z0-9_-]+", "-", plain).strip("-").lower() or fallback


def bundle(files: list[tuple[str, bytes | str]]) -> bytes:
    """ZIP mit den Dateien (Word + draw.io) fuer einen Download."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files:
            z.writestr(name, body.encode() if isinstance(body, str) else body)
    return buf.getvalue()


def _stamp(when: datetime, tenant: str | None) -> str:
    parts = [f"Stand {when:%d.%m.%Y %H:%M} UTC", f"Automato {__version__.split('+')[0]}"]
    if tenant:
        parts.append(f"Mandant {tenant}")
    return " · ".join(parts)


def _capped(doc: Document, rows: list, what: str) -> list:
    if len(rows) > MAX_ROWS:
        doc.note(f"{what}: die ersten {MAX_ROWS} von {len(rows)} – alle in Automato.")
        return rows[:MAX_ROWS]
    return rows


# -- Applikations-Doku ---------------------------------------------------------------------

# Abschnitte zur Wahl beim Export (0.73.0, wie im Export-Dialog der Oberflaeche); der Kopf mit
# Steckbrief steht immer da
SECTIONS = ("overview", "systems", "epgs", "connections", "plan", "rules", "categories",
            "hidden", "findings")  # fmt: skip


def application(doc: dict, conns: dict | None, sources: dict[int, str], when: datetime,
                tenant: str | None = None, plan_file: str | None = None,
                sections: set[str] | None = None) -> bytes:  # fmt: skip
    """Applikations-Doku als Word: `doc` wie `GET /applications/{id}/doc`, `conns` wie
    `GET …/connections` (None = ohne Verbindungen); `sections` = gewaehlte Abschnitte (None =
    alle, `SECTIONS`)."""
    want = set(SECTIONS) if sections is None else sections
    a = doc["application"]
    out = Document(f"Applikation {a['name']}", "Applikations-Doku (Automato)")
    out.heading(f"Applikation {a['name']}", 0)
    out.note(_stamp(when, tenant))
    site = a.get("site") or {}
    out.fields([
        ("Externer Name", a.get("external_name")),
        ("Interner Name", a.get("internal_name")),
        ("Zweck", PURPOSE.get(a.get("purpose") or "", a.get("purpose"))),
        ("Kürzel", a.get("code")),
        ("GG", doc.get("tenant_code")),
        ("Kritikalität", CRIT.get(a.get("criticality") or "", a.get("criticality"))),
        ("Verantwortlich", a.get("owner")),
        ("Standort", " · ".join(x for x in (site.get("code"), site.get("name")) if x)),
        ("Beschreibung", a.get("description")),
    ])  # fmt: skip
    s = doc.get("summary") or {}
    servers = f" + {s['servers']} Server" if s.get("servers") else ""
    if "overview" in want:
        out.paragraph(("Übersicht: ", {"bold": True}),
                      f"{s.get('vms', 0)} VMs{servers}"
                      f" · {s.get('epgs', 0)} EPGs · {s.get('rules', 0)} Regeln "
                      f"({s.get('specific', 0)} spezifisch, {s.get('unspecific', 0)} "
                      f"unspezifisch) · {s.get('findings', 0)} Befunde")  # fmt: skip
    if "systems" in want:
        _systems(out, doc)
    epgs = doc.get("epgs") or []
    if epgs and "epgs" in want:
        out.heading("EPGs im ACI", 1)
        out.table(["EPG", "BD", "VRF", "Subnetze", "VMs"],
                  [[e["label"], e.get("bd") or "", e.get("vrf") or "",
                    ", ".join(e.get("subnets") or []), str(e.get("vm_count") or 0)]
                   for e in epgs], [4, 2.4, 2.4, 3, 0.8])  # fmt: skip
    if "rules" in want:
        _rules(out, doc, sources)
    if conns is not None and "connections" in want:
        _connections(out, conns)
    if "plan" in want:
        out.heading("Netzwerkplan", 1)
        if plan_file:
            out.paragraph("Der Plan liegt als eigene Datei bei: ", (plan_file, {"bold": True}),
                          " – in Confluence mit der draw.io-App einfügen (Diagramm importieren) "
                          "bzw. in draw.io bearbeiten.")  # fmt: skip
        else:
            out.note("Netzwerkplan als draw.io-Datei in Automato (Verbindungen → Plan → Export).")
    if "categories" in want or "hidden" in want:
        _tables(out, doc.get("tables") or [], "categories" in want, "hidden" in want)
    if "findings" in want:
        _findings(out, doc.get("findings") or [])
    return out.bytes(when)


def _systems(out: Document, doc: dict) -> None:
    out.heading("Systeme", 1)
    rows = [[("VM " if x["kind"] == "vm" else "Server ") + x["name"], ", ".join(x.get("ips") or []),
             ", ".join(x.get("epgs") or []), ", ".join(x.get("portgroups") or []),
             f"{(x.get('rules') or {}).get('specific', 0)} / "
             f"{(x.get('rules') or {}).get('unspecific', 0)}"]
            for x in doc.get("systems") or []]  # fmt: skip
    if rows:
        out.table(["System", "IPs", "EPG", "Portgruppe", "Regeln spez. / unspez."],
                  _capped(out, rows, "Systeme"), [3, 3, 3, 3, 1.4])  # fmt: skip
    else:
        out.note("keine Systeme")


def _rules(out: Document, doc: dict, sources: dict[int, str]) -> None:
    names = {x["key"]: x["name"] for x in doc.get("systems") or []}
    names |= {e["key"]: e["label"] for e in doc.get("epgs") or []}
    rows = []
    for r in doc.get("rules") or []:
        hits = [h for h in doc.get("hits") or []
                if h["rule_id"] == r["id"] and h.get("relevance") != "impossible"]  # fmt: skip
        if not hits:
            continue  # routingtechnisch unmoeglich (0.24.0)
        systems = list(dict.fromkeys(names.get(h["system"], h["system"]) for h in hits))
        best = min((h.get("scope") or "any" for h in hits), key=lambda x: RANK.get(x, 9))
        if best == "assigned":
            who = "direkt zugeordnet"
        elif best == "specific":
            who = ", ".join(systems)
        else:
            who = f"{len(systems)} System{'' if len(systems) == 1 else 'e'}"
        direction = ", ".join(dict.fromkeys(DIRECTION.get(h["direction"], h["direction"])
                                            + (" (Rückrichtung)" if h.get("reverse") else "")
                                            for h in hits))  # fmt: skip
        via = ", ".join(dict.fromkeys(h.get("via") or "" for h in hits if h.get("via")))
        aci = r.get("aci") or {}
        origin = "ACI" if r.get("domain") == "aci_contract" else "Firewall"
        device = f"{origin} {sources.get(r.get('source_system_id'), '')}"
        rows.append([aci.get("contract") or r.get("name") or f"#{r.get('external_id')}",
                     r.get("action") or "", device,
                     direction, who, f"{SCOPE.get(best, best)}: {via}"])  # fmt: skip
    out.heading("Regeln & Contracts", 1)
    if rows:
        out.table(["Regel", "Aktion", "Herkunft", "Richtung", "System", "Bezug"],
                  _capped(out, rows, "Regeln"), [3, 1, 2.2, 1.6, 3, 3])  # fmt: skip
    else:
        out.note("keine Regeln, die die Systeme nach dem Routing erreichen")


def _dependencies(out: Document, deps: dict) -> None:
    """Abhaengigkeiten zu anderen Applikationen (0.60.0) - seit 0.73.0 nicht im Export (Anwender:
    vorerst entfernt, Monitoring u. a. sind keine fachlichen Abhaengigkeiten)."""
    rows = []
    for label, key in (("nutzt", "uses"), ("wird genutzt von", "used_by")):
        for d in deps.get(key) or []:
            rules = ", ".join(f"{r['name']} ({', '.join(r.get('services') or []) or 'alle'})"
                              for r in d.get("rules") or [])  # fmt: skip
            rows.append([label, d["name"], rules])
    if rows:
        out.heading("Abhängigkeiten", 1)
        out.table(["Richtung", "Applikation", "Regeln"], rows, [1.4, 2.6, 8])


def _side(s: dict) -> str:
    more = len(s.get("systems") or []) + (s.get("more") or 0)
    extra = f" +{more - 1}" if s.get("kind") == "system" and more > 1 else ""
    return f"{s.get('label')}{extra} ({s.get('ip')}{' · ' + s['name'] if s.get('name') else ''})"


def _hop(h: dict) -> str:
    deciding = []
    checks = h.get("checks") or []
    for c in checks:
        for r in c.get("rules") or []:
            if r.get("decides"):
                text = r.get("label") or r.get("name") or r.get("external_id") or "Regel"
                deciding.append((f"{c.get('label')}: " if len(checks) > 1 else "") + text)
    deciding = list(dict.fromkeys(deciding))
    return f"{h.get('label')} ({', '.join(deciding)})" if deciding else str(h.get("label"))


def _connections(out: Document, data: dict) -> None:
    out.heading("Verbindungen Ende zu Ende", 1)
    s = data.get("summary") or {}
    out.paragraph(f"{s.get('total', 0)} Verbindungen · {s.get('allow', 0)} erlaubt · "
                  f"{s.get('blocked', 0)} gesperrt · {s.get('open', 0)} abhängig bzw. offen · "
                  f"{s.get('mismatch', 0)} Abweichungen · {s.get('inconsistent', 0)} Wege "
                  "uneinheitlich")  # fmt: skip
    for n in data.get("notes") or []:
        out.note(n)
    rows = []
    for c in data.get("connections") or []:
        way = " → ".join(_hop(h) for h in c.get("hops") or [])
        if not c.get("reached") and c.get("end"):
            way += f" – {c['end'].get('text')}"
        rows.append([("eingehend" if c.get("direction") == "in" else "ausgehend")
                     + (" (Rückrichtung)" if c.get("reverse") else ""),
                     _side(c["src"]), _side(c["dst"]), c.get("service") or "alle", way or "–",
                     c.get("text") or VERDICT.get(c.get("verdict"), c.get("verdict") or ""),
                     _zones(c.get("zones")) or "–",
                     "; ".join((["Wege uneinheitlich"] if c.get("inconsistent") else [])
                               + (c.get("mismatch") or []))])  # fmt: skip
    if rows:
        out.table(["Richtung", "Quelle", "Ziel", "Dienst", "Weg (entscheidende Regel)", "Urteil",
                   "Zonen", "Abweichung"], _capped(out, rows, "Verbindungen"),
                  [1.2, 2.3, 2.3, 0.9, 3.6, 1.6, 2.2, 2.1])  # fmt: skip

    else:
        out.note("keine prüfbaren Verbindungen")


def _zones(z: dict | None) -> str:
    """Zonen einer Verbindung (0.78.0): Kette des Wegs und die Absichten mit Urteil."""
    if not z:
        return ""
    names = [e["name"] for e in z.get("chain") or []] or [*z.get("src", []), *z.get("dst", [])]
    way = " → ".join(dict.fromkeys(names))
    return " · ".join(x for x in (way, "; ".join(i["text"] for i in z.get("intents") or [])) if x)


def _tables(out: Document, tables: list[dict], rows_too: bool = True,
            hidden_too: bool = True) -> None:  # fmt: skip
    out.heading("Doku nach Kategorien" if rows_too else "Ausgeblendete Regeln", 1)
    empty = []
    for t in tables if rows_too else []:
        if not t.get("rows"):
            empty.append(f"{t['index']}. {t['title']}")
            continue
        out.heading(f"{t['index']}. {t['title']}", 2)
        cols = t.get("columns") or []
        rows = [[str(row.get(c) if row.get(c) is not None else "") for c in cols]
                for row in t["rows"]]  # fmt: skip
        out.table(cols, _capped(out, rows, t["title"]))
    if empty:
        out.note("ohne Einträge: " + ", ".join(empty))
    hidden = [(t, row) for t in tables for row in t.get("hidden") or []] if hidden_too else []
    if not rows_too and not hidden:
        out.note("keine ausgeblendeten Regeln")
    if hidden:
        if rows_too:
            out.heading("Ausgeblendete Regeln", 2)
        rows = []
        for t, row in hidden:
            hides = row.get("_hides") or []
            rows.append([f"{t['index']}. {t['title']}", str(row.get("Description") or ""),
                         str(row.get("Server Name") or row.get("Source") or ""),
                         "; ".join(h.get("reason") or "" for h in hides),
                         ", ".join(dict.fromkeys(h.get("by") or "" for h in hides)),
                         (hides[0].get("at") or "")[:10] if hides else ""])  # fmt: skip
        out.table(["Kategorie", "Regel", "System bzw. Objekt", "Begründung", "von", "am"], rows,
                  [2.2, 2.6, 2.4, 4, 1.4, 1])  # fmt: skip


def _findings(out: Document, findings: list[dict]) -> None:
    if not findings:
        return
    out.heading("Befunde", 1)
    rows = [[SEVERITY.get(f["severity"], f["severity"]), f["subject_id"], f["message"],
             f.get("hint") or ""] for f in findings]  # fmt: skip
    out.table(["Schwere", "Gegenstand", "Befund", "Hinweis"],
              _capped(out, rows, "Befunde"), [1, 3, 5, 4])  # fmt: skip


# -- Zonenplan -----------------------------------------------------------------------------


def zone_plan(plan: dict, matrix: dict | None, title: str, when: datetime,
              tenant: str | None = None, plan_file: str | None = None,
              version: dict | None = None) -> bytes:  # fmt: skip
    """Zonenplan als Word: `plan` wie `GET /zones/plan` (Format 2), `matrix` wie
    `GET /zones/matrix`."""
    out = Document(title, "Zonenplan (Automato)")
    out.heading(title, 0)
    stamp = _stamp(when, tenant)
    if version:
        stamp += f" · Version {version.get('number')}" + (
            f" „{version['label']}“" if version.get("label") else ""
        )
    out.note(stamp)
    view = plan.get("view") or {}
    if view.get("customer"):
        out.paragraph(("Ansicht: ", {"bold": True}), f"Kunde {view['customer']}")
    if plan_file:
        out.paragraph("Der Plan liegt als eigene Datei bei: ", (plan_file, {"bold": True}),
                      " – in Confluence mit der draw.io-App einfügen bzw. in draw.io "
                      "bearbeiten.")  # fmt: skip
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    zones = [z for z in plan.get("zones") or [] if isinstance(z.get("id"), int)]
    out.heading("Zonen", 1)
    if zones:
        rows = []
        for z in zones:
            samples = list(z.get("samples") or [])
            nets = ", ".join(samples)
            if (z.get("nets") or 0) > len(samples):
                nets += f" … ({z['nets']})"
            rows.append([z["name"], z.get("code") or "", ZONE_KIND.get(z.get("kind"), z.get("kind")
                         or ""), ", ".join(nodes.get(n, n) for n in z.get("nodes") or []) or "–",
                         nets or "–"])  # fmt: skip
        out.table(["Zone", "Kurzname", "Art", "Knoten", "Netze in der Zone"],
                  _capped(out, rows, "Zonen"), [2.4, 1.2, 1.4, 3.4, 4])  # fmt: skip
    else:
        out.note("keine Zonen")

    rows = []
    for t in plan.get("transitions") or []:
        for n in t.get("networks") or [{}]:
            via = ", ".join(
                f"{leg['label']} ({'L3Out' if leg.get('l3out') else 'Interface'} "
                f"{leg.get('interface') or '?'})"
                for leg in n.get("legs") or []
            )
            routers = ", ".join((r.get("names") or [r.get("ip")])[0] or ""
                                for r in n.get("routers") or [])  # fmt: skip
            rows.append([t.get("name") or "", n.get("network") or "", via, routers])
    if rows:
        out.heading("Übergänge", 1)
        out.table(["Übergang", "Netz", "über", "Router"], _capped(out, rows, "Übergänge"),
                  [2.6, 1.8, 5, 2.4])  # fmt: skip
    rows = plan_export.intent_rows(plan)  # 0.78.0
    if rows:
        out.heading("Absicht je Richtung", 1)
        out.table(["Von", "Nach", "Absicht", "Dienste", "Linie"],
                  _capped(out, rows, "Absichten"), [2.6, 2.6, 1.6, 3.4, 1.8])  # fmt: skip

    if matrix:
        _matrix(out, matrix)
    unzoned = plan.get("unzoned") or {}
    rest = [[u["network"], u.get("node") or "", u.get("where") or ""]
            for u in unzoned.get("items") or []]  # fmt: skip
    if rest:
        out.heading("Netze ohne Zone", 1)
        if (unzoned.get("total") or 0) > len(rest):
            out.note(f"die ersten {len(rest)} von {unzoned['total']}")
        out.table(["Netz", "Knoten", "Interface / Ort"], rest, [2, 3, 4])
    return out.bytes(when)


def _matrix(out: Document, matrix: dict) -> None:
    zones = {z["id"]: z for z in matrix.get("zones") or []}

    def name(c) -> str:
        return {"any": "any", "none": "ohne Zone", "other": "anderer Kunde"}.get(
            c, zones.get(c, {}).get("name", f"#{c}")
        )

    out.heading("Zonenmatrix", 1)
    s = matrix.get("summary") or {}
    out.paragraph(f"{s.get('assigned', 0)} von {s.get('rules', 0)} Regeln zugeordnet · "
                  f"{s.get('violations', 0)} gegen die Absicht. Je Paar Quell- → Zielzone die "
                  "erlaubenden und sperrenden Regeln und die Absicht.")  # fmt: skip
    rows = []
    for c in matrix.get("cells") or []:
        if not (c.get("allow") or c.get("deny") or c.get("intent")):
            continue
        intent = INTENT.get(c.get("intent") or "", "–")
        if c.get("services"):
            intent += f" ({', '.join(c['services'])})"
        rows.append([name(c["src"]), name(c["dst"]), intent, str(c.get("allow") or 0),
                     str(c.get("deny") or 0), str(c.get("violations") or 0),
                     c.get("note") or ""])  # fmt: skip
    if rows:
        out.table(["Von", "Nach", "Absicht", "erlaubend", "sperrend", "gegen die Absicht",
                   "Notiz"], _capped(out, rows, "Paare"),
                  [2.4, 2.4, 2.4, 1, 1, 1.2, 3])  # fmt: skip
    else:
        out.note("keine Regeln zwischen den Zonen")

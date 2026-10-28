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
from app.i18n import L
from app.store import appdoc, plan_export
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
# Englisch (0.87.0, ADR 0028): die deutschen Tabellen oben bleiben, gewaehlt wird je Anfrage
_EN = {
    "crit": {"low": "low", "medium": "medium", "high": "high", "critical": "critical"},
    "scope": {"assigned": "assigned", "specific": "specific", "subnet": "subnet"},
    "direction": {"assigned": "assigned", "in": "inbound", "out": "outbound"},
    "verdict": {"allow": "allowed", "deny": "blocked", "implicit_deny": "implicitly denied",
                "depends": "depends", "unknown": "unknown"},
    "severity": {"error": "Error", "warning": "Warning", "info": "Note"},
    "intent": {"allow": "allow", "limited": "limited", "deny": "deny"},
    "zone_kind": {"external": "Public / external", "internal": "internal", "shared": "shared"},
}  # fmt: skip


def _pick(table: dict, en: str, key, default=None):
    """Wert einer Tabelle in der Sprache der Anfrage (unbekannt = `default`)."""
    de = table.get(key)
    if de is None:
        return default
    return L(de, _EN[en].get(key, de))


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
    parts = [L(f"Stand {when:%d.%m.%Y %H:%M} UTC", f"As of {when:%d/%m/%Y %H:%M} UTC"),
             f"Automato {__version__.split('+')[0]}"]  # fmt: skip
    if tenant:
        parts.append(L(f"Mandant {tenant}", f"Tenant {tenant}"))
    return " · ".join(parts)


def _capped(doc: Document, rows: list, what: str) -> list:
    if len(rows) > MAX_ROWS:
        doc.note(
            L(
                f"{what}: die ersten {MAX_ROWS} von {len(rows)} – alle in Automato.",
                f"{what}: the first {MAX_ROWS} of {len(rows)} – all in Automato.",
            )
        )
        return rows[:MAX_ROWS]
    return rows


# -- Applikations-Doku ---------------------------------------------------------------------

# Abschnitte zur Wahl beim Export (0.73.0, wie im Export-Dialog der Oberflaeche); der Kopf mit
# Steckbrief steht immer da
SECTIONS = ("overview", "systems", "epgs", "connections", "plan", "rules", "dependencies",
            "categories", "hidden", "findings")  # fmt: skip


def application(doc: dict, conns: dict | None, sources: dict[int, str], when: datetime,
                tenant: str | None = None, plan_file: str | None = None,
                sections: set[str] | None = None) -> bytes:  # fmt: skip
    """Applikations-Doku als Word: `doc` wie `GET /applications/{id}/doc`, `conns` wie
    `GET …/connections` (None = ohne Verbindungen); `sections` = gewaehlte Abschnitte (None =
    alle, `SECTIONS`)."""
    want = set(SECTIONS) if sections is None else sections
    a = doc["application"]
    title = f"{L('Applikation', 'Application')} {a['name']}"
    out = Document(title, L("Applikations-Doku (Automato)", "Application documentation (Automato)"))
    out.heading(title, 0)
    out.note(_stamp(when, tenant))
    site = a.get("site") or {}
    out.fields([
        (L("Externer Name", "External name"), a.get("external_name")),
        (L("Interner Name", "Internal name"), a.get("internal_name")),
        (L("Zweck", "Purpose"), PURPOSE.get(a.get("purpose") or "", a.get("purpose"))),
        (L("Kürzel", "Code"), a.get("code")),
        (L("GG", "GC"), doc.get("tenant_code")),
        (L("Kritikalität", "Criticality"),
         _pick(CRIT, "crit", a.get("criticality") or "", a.get("criticality"))),
        (L("Verantwortlich", "Owner"), a.get("owner")),
        (L("Standort", "Site"), " · ".join(x for x in (site.get("code"), site.get("name")) if x)),
        (L("Beschreibung", "Description"), a.get("description")),
    ])  # fmt: skip
    s = doc.get("summary") or {}
    if "overview" in want:
        servers = L(f" + {s['servers']} Server", f" + {s['servers']} servers") \
            if s.get("servers") else ""  # fmt: skip
        out.paragraph((L("Übersicht: ", "Overview: "), {"bold": True}), L(
            f"{s.get('vms', 0)} VMs{servers}"
            f" · {s.get('epgs', 0)} EPGs · {s.get('rules', 0)} Regeln "
            f"({s.get('specific', 0)} spezifisch, {s.get('unspecific', 0)} "
            f"unspezifisch) · {s.get('findings', 0)} Befunde",
            f"{s.get('vms', 0)} VMs{servers}"
            f" · {s.get('epgs', 0)} EPGs · {s.get('rules', 0)} rules "
            f"({s.get('specific', 0)} specific, {s.get('unspecific', 0)} "
            f"unspecific) · {s.get('findings', 0)} findings"))  # fmt: skip
    if "systems" in want:
        _systems(out, doc)
    epgs = doc.get("epgs") or []
    if epgs and "epgs" in want:
        out.heading(L("EPGs im ACI", "EPGs in the ACI"), 1)
        out.table(["EPG", "BD", "VRF", L("Subnetze", "Subnets"), "VMs"],
                  [[e["label"], e.get("bd") or "", e.get("vrf") or "",
                    ", ".join(e.get("subnets") or []), str(e.get("vm_count") or 0)]
                   for e in epgs], [4, 2.4, 2.4, 3, 0.8])  # fmt: skip
    if "rules" in want:
        _rules(out, doc, sources)
    if "dependencies" in want and doc.get("dependencies"):
        _dependencies(out, doc["dependencies"])
    if conns is not None and "connections" in want:
        _connections(out, conns)
    if "plan" in want:
        out.heading(L("Netzwerkplan", "Network diagram"), 1)
        if plan_file:
            out.paragraph(L("Der Plan liegt als eigene Datei bei: ",
                            "The diagram is attached as a separate file: "),
                          (plan_file, {"bold": True}),
                          L(" – in Confluence mit der draw.io-App einfügen (Diagramm importieren) "
                            "bzw. in draw.io bearbeiten.",
                            " – insert it in Confluence with the draw.io app (import diagram) "
                            "or edit it in draw.io."))  # fmt: skip
        else:
            out.note(L("Netzwerkplan als draw.io-Datei in Automato (Verbindungen → Plan → Export).",
                       "Network diagram as a draw.io file in Automato (Connections → Diagram → "
                       "Export)."))  # fmt: skip
    if "categories" in want or "hidden" in want:
        _tables(out, doc.get("tables") or [], "categories" in want, "hidden" in want)
    if "findings" in want:
        _findings(out, doc.get("findings") or [])
    return out.bytes(when)


def _systems(out: Document, doc: dict) -> None:
    out.heading(L("Systeme", "Systems"), 1)
    rows = [[("VM " if x["kind"] == "vm" else "Server ") + x["name"], ", ".join(x.get("ips") or []),
             ", ".join(x.get("epgs") or []), ", ".join(x.get("portgroups") or []),
             f"{(x.get('rules') or {}).get('specific', 0)} / "
             f"{(x.get('rules') or {}).get('unspecific', 0)}"]
            for x in doc.get("systems") or []]  # fmt: skip
    if rows:
        out.table(["System", "IPs", "EPG", L("Portgruppe", "Port group"),
                   L("Regeln spez. / unspez.", "Rules spec. / unspec.")],
                  _capped(out, rows, L("Systeme", "Systems")), [3, 3, 3, 3, 1.4])  # fmt: skip
    else:
        out.note(L("keine Systeme", "no systems"))


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
            who = L("direkt zugeordnet", "assigned directly")
        elif best == "specific":
            who = ", ".join(systems)
        else:
            n = len(systems)
            who = L(f"{n} System{'' if n == 1 else 'e'}", f"{n} system{'' if n == 1 else 's'}")
        direction = ", ".join(dict.fromkeys(
            _pick(DIRECTION, "direction", h["direction"], h["direction"])
            + (f" {appdoc.reverse_mark()}" if h.get("reverse") else "")
            for h in hits))  # fmt: skip
        via = ", ".join(dict.fromkeys(h.get("via") or "" for h in hits if h.get("via")))
        aci = r.get("aci") or {}
        origin = "ACI" if r.get("domain") == "aci_contract" else "Firewall"
        device = f"{origin} {sources.get(r.get('source_system_id'), '')}"
        rows.append([aci.get("contract") or r.get("name") or f"#{r.get('external_id')}",
                     r.get("action") or "", device,
                     direction, who, f"{_pick(SCOPE, 'scope', best, best)}: {via}"])  # fmt: skip
    out.heading(L("Regeln & Contracts", "Rules & contracts"), 1)
    if rows:
        out.table([L("Regel", "Rule"), L("Aktion", "Action"), L("Herkunft", "Origin"),
                   L("Richtung", "Direction"), "System", L("Bezug", "Reference")],
                  _capped(out, rows, L("Regeln", "Rules")), [3, 1, 2.2, 1.6, 3, 3])  # fmt: skip
    else:
        out.note(
            L(
                "keine Regeln, die die Systeme nach dem Routing erreichen",
                "no rules that reach the systems according to the routing",
            )
        )


def _dependencies(out: Document, deps: dict) -> None:
    """Abhaengigkeiten zu anderen Applikationen (0.60.0, seit 0.88.0 wieder im Export): zuerst
    die fachlichen, danach die zu Infrastruktur-Applikationen (Monitoring, Backup, DNS u. a.)."""
    out.heading(L("Abhängigkeiten", "Dependencies"), 1)
    every = L("alle", "all")
    for infra, title in ((False, None), (True, L("Infrastruktur", "Infrastructure"))):
        rows = []
        for label, key in ((L("nutzt", "uses"), "uses"),
                           (L("wird genutzt von", "used by"), "used_by")):  # fmt: skip
            for d in deps.get(key) or []:
                if bool(d.get("infrastructure")) != infra:
                    continue
                rules = ", ".join(f"{r['name']} ({', '.join(r.get('services') or []) or every})"
                                  for r in d.get("rules") or [])  # fmt: skip
                rows.append([label, d["name"], rules])
        if not rows:
            continue
        if title:
            out.heading(title, 2)
        out.table([L("Richtung", "Direction"), L("Applikation", "Application"),
                   L("Regeln", "Rules")], rows, [1.4, 2.6, 8])  # fmt: skip
    if not deps.get("uses") and not deps.get("used_by"):
        out.note(L("keine Abhängigkeiten zu anderen Applikationen",
                   "no dependencies on other applications"))  # fmt: skip


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
                text = r.get("label") or r.get("name") or r.get("external_id") or L("Regel", "Rule")
                deciding.append((f"{c.get('label')}: " if len(checks) > 1 else "") + text)
    deciding = list(dict.fromkeys(deciding))
    return f"{h.get('label')} ({', '.join(deciding)})" if deciding else str(h.get("label"))


def _connections(out: Document, data: dict) -> None:
    out.heading(L("Verbindungen Ende zu Ende", "End-to-end connections"), 1)
    s = data.get("summary") or {}
    out.paragraph(L(f"{s.get('total', 0)} Verbindungen · {s.get('allow', 0)} erlaubt · "
                    f"{s.get('blocked', 0)} gesperrt · {s.get('open', 0)} abhängig bzw. offen · "
                    f"{s.get('mismatch', 0)} Abweichungen · {s.get('inconsistent', 0)} Wege "
                    "uneinheitlich",
                    f"{s.get('total', 0)} connections · {s.get('allow', 0)} allowed · "
                    f"{s.get('blocked', 0)} blocked · {s.get('open', 0)} depends or open · "
                    f"{s.get('mismatch', 0)} deviations · {s.get('inconsistent', 0)} paths "
                    "inconsistent"))  # fmt: skip
    for n in data.get("notes") or []:
        out.note(n)
    rows = []
    for c in data.get("connections") or []:
        way = " → ".join(_hop(h) for h in c.get("hops") or [])
        if not c.get("reached") and c.get("end"):
            way += f" – {c['end'].get('text')}"
        direction = L("eingehend", "inbound") if c.get("direction") == "in" \
            else L("ausgehend", "outbound")  # fmt: skip
        rows.append([direction + (f" {appdoc.reverse_mark()}" if c.get("reverse") else ""),
                     _side(c["src"]), _side(c["dst"]), c.get("service") or L("alle", "all"),
                     way or "–",
                     c.get("text") or _pick(VERDICT, "verdict", c.get("verdict"),
                                            c.get("verdict") or ""),
                     _zones(c.get("zones")) or "–",
                     "; ".join(([L("Wege uneinheitlich", "paths inconsistent")]
                                if c.get("inconsistent") else [])
                               + (c.get("mismatch") or []))])  # fmt: skip
    if rows:
        out.table([L("Richtung", "Direction"), L("Quelle", "Source"), L("Ziel", "Destination"),
                   L("Dienst", "Service"), L("Weg (entscheidende Regel)", "Path (deciding rule)"),
                   L("Urteil", "Verdict"), L("Zonen", "Zones"), L("Abweichung", "Deviation")],
                  _capped(out, rows, L("Verbindungen", "Connections")),
                  [1.2, 2.3, 2.3, 0.9, 3.6, 1.6, 2.2, 2.1])  # fmt: skip

    else:
        out.note(L("keine prüfbaren Verbindungen", "no checkable connections"))


def _zones(z: dict | None) -> str:
    """Zonen einer Verbindung (0.78.0): Kette des Wegs und die Absichten mit Urteil."""
    if not z:
        return ""
    names = [e["name"] for e in z.get("chain") or []] or [*z.get("src", []), *z.get("dst", [])]
    way = " → ".join(dict.fromkeys(names))
    return " · ".join(x for x in (way, "; ".join(i["text"] for i in z.get("intents") or [])) if x)


def zones_text(zones: list[dict]) -> str:
    """Zonen einer Kategorie (0.101.0): „Zonen: Clients (10.70.0.0/16, …) · …“; seit 0.102.0
    mit den Ausschluessen („10.0.0.0/8 ohne 10.20.0.0/16“)."""
    parts = []
    for z in zones:
        more = z.get("nets_total", 0) - len(z.get("nets") or [])
        nets = ", ".join(z.get("nets") or []) + (
            L(f" +{more} weitere", f" +{more} more") if more > 0 else ""
        )
        if z.get("excluded"):
            listed = ", ".join(z["excluded"])
            nets += L(f" ohne {listed}", f" without {listed}")
        parts.append(f"{z['name']} ({nets})" if nets else z["name"])
    return L("Zonen: ", "Zones: ") + " · ".join(parts)


def _tables(out: Document, tables: list[dict], rows_too: bool = True,
            hidden_too: bool = True) -> None:  # fmt: skip
    hidden_title = L("Ausgeblendete Regeln", "Hidden rules")
    out.heading(L("Doku nach Kategorien", "Documentation by category") if rows_too
                else hidden_title, 1)  # fmt: skip
    empty = []
    for t in tables if rows_too else []:
        if not t.get("rows"):
            empty.append(f"{t['index']}. {t['title']}")
            continue
        out.heading(f"{t['index']}. {t['title']}", 2)
        if t.get("zones"):  # 0.101.0: Zonen der Kategorie mit ihren Netzen
            out.note(zones_text(t["zones"]))
        cols = t.get("columns") or []
        # Anzeige der Spalten (0.87.0): `labels` parallel zu den Schluesseln `columns`
        labels = t.get("labels") or appdoc.column_labels(cols)
        rows = [[str(row.get(c) if row.get(c) is not None else "") for c in cols]
                for row in t["rows"]]  # fmt: skip
        out.table(labels, _capped(out, rows, t["title"]))
    if empty:
        out.note(L("ohne Einträge: ", "no entries: ") + ", ".join(empty))
    hidden = [(t, row) for t in tables for row in t.get("hidden") or []] if hidden_too else []
    if not rows_too and not hidden:
        out.note(L("keine ausgeblendeten Regeln", "no hidden rules"))
    if hidden:
        if rows_too:
            out.heading(hidden_title, 2)
        rows = []
        for t, row in hidden:
            hides = row.get("_hides") or []
            rows.append([f"{t['index']}. {t['title']}", str(row.get("Description") or ""),
                         str(row.get("_app") or "").replace("\n", ", "),
                         "; ".join(h.get("reason") or "" for h in hides),
                         ", ".join(dict.fromkeys(h.get("by") or "" for h in hides)),
                         (hides[0].get("at") or "")[:10] if hides else ""])  # fmt: skip
        out.table([L("Kategorie", "Category"), L("Regel", "Rule"),
                   L("System bzw. Objekt", "System or object"), L("Begründung", "Reason"),
                   L("von", "by"), L("am", "on")], rows, [2.2, 2.6, 2.4, 4, 1.4, 1])  # fmt: skip


def _findings(out: Document, findings: list[dict]) -> None:
    if not findings:
        return
    out.heading(L("Befunde", "Findings"), 1)
    rows = [[_pick(SEVERITY, "severity", f["severity"], f["severity"]), f["subject_id"],
             f["message"], f.get("hint") or ""] for f in findings]  # fmt: skip
    out.table([L("Schwere", "Severity"), L("Gegenstand", "Subject"), L("Befund", "Finding"),
               L("Hinweis", "Note")], _capped(out, rows, L("Befunde", "Findings")),
              [1, 3, 5, 4])  # fmt: skip


# -- Zonenplan -----------------------------------------------------------------------------


def zone_plan(plan: dict, matrix: dict | None, title: str, when: datetime,
              tenant: str | None = None, plan_file: str | None = None,
              version: dict | None = None) -> bytes:  # fmt: skip
    """Zonenplan als Word: `plan` wie `GET /zones/plan` (Format 2), `matrix` wie
    `GET /zones/matrix`."""
    out = Document(title, L("Zonenplan (Automato)", "Zone plan (Automato)"))
    out.heading(title, 0)
    stamp = _stamp(when, tenant)
    if version:
        label = version.get("label")
        stamp += f" · Version {version.get('number')}" + (
            L(f" „{label}“", f" “{label}”") if label else ""
        )
    out.note(stamp)
    view = plan.get("view") or {}
    if view.get("customer"):
        out.paragraph(
            (L("Ansicht: ", "View: "), {"bold": True}),
            L(f"Kunde {view['customer']}", f"Customer {view['customer']}"),
        )
    if plan_file:
        out.paragraph(L("Der Plan liegt als eigene Datei bei: ",
                        "The plan is attached as a separate file: "),
                      (plan_file, {"bold": True}),
                      L(" – in Confluence mit der draw.io-App einfügen bzw. in draw.io "
                        "bearbeiten.",
                        " – insert it in Confluence with the draw.io app or edit it in "
                        "draw.io."))  # fmt: skip
    nodes = {n["id"]: n["label"] for n in plan.get("nodes") or []}
    zones = [z for z in plan.get("zones") or [] if isinstance(z.get("id"), int)]
    out.heading(L("Zonen", "Zones"), 1)
    if zones:
        rows = []
        for z in zones:
            samples = list(z.get("samples") or [])
            nets = ", ".join(samples)
            if (z.get("nets") or 0) > len(samples):
                nets += f" … ({z['nets']})"
            rows.append([z["name"], z.get("code") or "",
                         _pick(ZONE_KIND, "zone_kind", z.get("kind"), z.get("kind") or ""),
                         ", ".join(nodes.get(n, n) for n in z.get("nodes") or []) or "–",
                         nets or "–"])  # fmt: skip
        out.table(["Zone", L("Kurzname", "Short name"), L("Art", "Kind"), L("Knoten", "Nodes"),
                   L("Netze in der Zone", "Networks in the zone")],
                  _capped(out, rows, L("Zonen", "Zones")), [2.4, 1.2, 1.4, 3.4, 4])  # fmt: skip
    else:
        out.note(L("keine Zonen", "no zones"))

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
        out.heading(L("Übergänge", "Transitions"), 1)
        out.table([L("Übergang", "Transition"), L("Netz", "Network"), L("über", "via"), "Router"],
                  _capped(out, rows, L("Übergänge", "Transitions")),
                  [2.6, 1.8, 5, 2.4])  # fmt: skip
    rows = plan_export.intent_rows(plan)  # 0.78.0
    if rows:
        out.heading(L("Absicht je Richtung", "Intent per direction"), 1)
        out.table([L("Von", "From"), L("Nach", "To"), L("Absicht", "Intent"),
                   L("Dienste", "Services"), L("Linie", "Line")],
                  _capped(out, rows, L("Absichten", "Intents")),
                  [2.6, 2.6, 1.6, 3.4, 1.8])  # fmt: skip

    if matrix:
        _matrix(out, matrix)
    unzoned = plan.get("unzoned") or {}
    rest = [[u["network"], u.get("node") or "", u.get("where") or ""]
            for u in unzoned.get("items") or []]  # fmt: skip
    if rest:
        out.heading(L("Netze ohne Zone", "Networks without zone"), 1)
        if (unzoned.get("total") or 0) > len(rest):
            out.note(
                L(
                    f"die ersten {len(rest)} von {unzoned['total']}",
                    f"the first {len(rest)} of {unzoned['total']}",
                )
            )
        out.table([L("Netz", "Network"), L("Knoten", "Node"),
                   L("Interface / Ort", "Interface / location")], rest, [2, 3, 4])  # fmt: skip
    return out.bytes(when)


def _matrix(out: Document, matrix: dict) -> None:
    zones = {z["id"]: z for z in matrix.get("zones") or []}

    special = {"any": "any", "none": L("ohne Zone", "no zone"),
               "other": L("anderer Kunde", "other customer")}  # fmt: skip

    def name(c) -> str:
        return special.get(c, zones.get(c, {}).get("name", f"#{c}"))

    out.heading(L("Zonenmatrix", "Zone matrix"), 1)
    s = matrix.get("summary") or {}
    out.paragraph(L(f"{s.get('assigned', 0)} von {s.get('rules', 0)} Regeln zugeordnet · "
                    f"{s.get('violations', 0)} gegen die Absicht. Je Paar Quell- → Zielzone die "
                    "erlaubenden und sperrenden Regeln und die Absicht.",
                    f"{s.get('assigned', 0)} of {s.get('rules', 0)} rules assigned · "
                    f"{s.get('violations', 0)} against the intent. Per pair source → destination "
                    "zone the allowing and denying rules and the intent."))  # fmt: skip
    rows = []
    for c in matrix.get("cells") or []:
        if not (c.get("allow") or c.get("deny") or c.get("intent")):
            continue
        intent = _pick(INTENT, "intent", c.get("intent") or "", "–")
        if c.get("services"):
            intent += f" ({', '.join(c['services'])})"
        rows.append([name(c["src"]), name(c["dst"]), intent, str(c.get("allow") or 0),
                     str(c.get("deny") or 0), str(c.get("violations") or 0),
                     c.get("note") or ""])  # fmt: skip
    if rows:
        out.table([L("Von", "From"), L("Nach", "To"), L("Absicht", "Intent"),
                   L("erlaubend", "allowing"), L("sperrend", "denying"),
                   L("gegen die Absicht", "against the intent"), L("Notiz", "Note")],
                  _capped(out, rows, L("Paare", "Pairs")),
                  [2.4, 2.4, 2.4, 1, 1, 1.2, 3])  # fmt: skip
    else:
        out.note(L("keine Regeln zwischen den Zonen", "no rules between the zones"))

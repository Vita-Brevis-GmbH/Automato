"""NetBox-Sync: Kabelnummern als Label der Kabel (1.19.0, ADR 0031 Nachtrag 1.19.0).

Anwender 2026-11-17: „Kabelnummern können auch in Automato erfasst werden, beides möglich“. Der
Abschnitt *Verkabelung* des NetBox-Syncs schreibt die in Automato erfasste Nummer
(`cable_label`) als `label` des Kabels - ueber den bestehenden Schreibweg (ADR 0010):

- **Neues Kabel** (legt der Sync im selben Durchgang an): die Nummer geht mit dem Kabel.
- **Bestehendes Kabel** (eigenes mit Tag `automato` oder von Hand gepflegt): eine Aktion
  `cable_label` „aendern“ mit beiden Werten (NetBox alt, Automato neu). Uebernommen wird sie nur,
  wenn der Betreiber sie in der Vorschau **bestaetigt** (`confirm` beim Uebernehmen) - auch an
  einem von Hand gepflegten Kabel, und dort nur das Feld `label`. Unbestaetigte bleiben, wie sie
  in NetBox sind.
- **Nur in NetBox** stehende Nummern bleiben unberuehrt (nie geloescht); die Vorschau zaehlt sie.
- Ohne Kabel in NetBox (und ohne neues aus der Verkabelung) bleibt die Nummer in Automato - ein
  Hinweis zaehlt sie; Kabel legt der Sync nur aus beidseitig gemeldeten Verbindungen an.

Die Paare werden normalisiert verglichen (`cable_labels.pair_key`: Geraet ohne Domaene, Port in
Kurzform), denn von Hand gepflegte Kabel tragen oft die Langform der Portnamen.
"""

from sqlalchemy.orm import Session

from app.store import cable_labels

KIND = "cable_label"


def _end(text: str) -> tuple[str, str]:
    device, _, port = text.partition("/")
    return device, port


def _pair_of_key(key: str) -> str | None:
    """Want-Schluessel eines Kabels (`<Geraet>/<Port>~<Geraet>/<Port>`) -> normalisiertes Paar."""
    a, sep, b = key.partition("~")
    if not sep:
        return None
    return cable_labels.pair_key(*_end(a), *_end(b))


def add_wants(session: Session, w, tenant) -> None:
    """Kabelnummern des Mandanten: am neuen Kabel als Feld, sonst als eigene Aktion; die Geraete
    der Enden kommen zu den Kabel-Enden (ihre Kabel liest der Sync aus NetBox)."""
    from app.store.netbox_sync import Want

    rows = cable_labels.labels(session, tenant.id)
    if not rows:
        return
    cables = {_pair_of_key(x.key): x for x in w.items.values() if x.kind == "cable"}
    for pair, row in sorted(rows.items()):
        cable = cables.get(pair)
        if cable is not None:
            cable.fields["label"] = row.label  # neues bzw. eigenes Kabel: Nummer mit dem Kabel
        text = f"{row.a_device} {row.a_port} ↔ {row.b_device} {row.b_port}"
        w.add(Want(KIND, pair, f"Kabelnummer {text}", {"label": row.label}))
        w.cable_ends |= {row.a_device, row.b_device}


def _state_pairs(state) -> dict[str, object]:
    """Kabel in NetBox (gelesen fuer die Verkabelung) nach normalisiertem Paar."""
    out = {}
    for key, have in state["cable"].items():
        pair = _pair_of_key(key)
        if pair is not None:
            out.setdefault(pair, have)
    return out


def items(wants, state) -> list[dict]:
    """Aktionen der Kabelnummern: unveraendert bzw. aendern (mit Bestaetigung). Hinweise fuer
    Nummern ohne Kabel in NetBox und Nummern nur in NetBox."""
    from app.store.netbox_sync import _examples, _item

    by_pair = _state_pairs(state)
    new = {_pair_of_key(x.key) for x in wants.items.values()
           if x.kind == "cable" and x.key not in state["cable"]}  # fmt: skip
    mine = {w.key for w in wants.items.values() if w.kind == KIND}
    out: list[dict] = []
    missing: list[str] = []
    for want in sorted((w for w in wants.items.values() if w.kind == KIND), key=lambda w: w.key):
        label = want.label.removeprefix("Kabelnummer ")
        have = by_pair.get(want.key)
        if have is None:
            if want.key not in new:
                missing.append(label)
            continue  # neues Kabel: die Nummer geht mit dem Kabel
        old = " ".join(str(have.obj.get("label") or "").split())
        if old == want.fields["label"]:
            out.append(_item(KIND, want.key, want.label, "unchanged", id=have.id))
            continue
        change = [{"field": "label", "old": old or None, "new": want.fields["label"]}]
        out.append(_item(KIND, want.key, want.label, "update", id=have.id, manual=not have.tagged,
                         confirm=True, changes=change))  # fmt: skip
    only_netbox = sorted(p for p, h in by_pair.items() if p not in mine and h.obj.get("label"))
    if missing:
        wants.warnings.append(
            f"Kabelnummern: {len(missing)} ohne Kabel in NetBox nicht übernommen (z. B. "
            f"{_examples(missing)}) – Kabel entsteht nur aus beidseitig gemeldeten Verbindungen."
        )
    if only_netbox:
        wants.warnings.append(
            f"Kabelnummern: {len(only_netbox)} nur in NetBox – bleiben, wie sie sind."
        )
    return out


def unconfirmed(items: list[dict], confirm: set[str]) -> set[tuple[str, str]]:
    """Aktionen, die der Betreiber nicht bestaetigt hat: (Art, Schluessel)."""
    asked = [i for i in items if i.get("confirm") and i["action"] == "update"]
    return {(i["kind"], i["key"]) for i in asked if i["key"] not in confirm}


def written(items: list[dict], confirm: set[str]) -> list[dict]:
    """Was die Uebernahme geschrieben hat (Nachkontrolle): ohne unbestaetigte Kabelnummern."""
    skip = unconfirmed(items, confirm)
    return [i for i in items if (i["kind"], i["key"]) not in skip]


def needs_confirm(items: list[dict]) -> list[str]:
    return sorted(i["key"] for i in items if i.get("confirm") and i["action"] == "update")

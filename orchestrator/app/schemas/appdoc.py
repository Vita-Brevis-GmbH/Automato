"""Ausgabe-Modelle der Applikations-Doku (seit 0.107.0 hier: die Doku je System samt
`api/appdoc.py` ist entfernt, die Applikations-Doku nutzt die Modelle weiter)."""

from pydantic import BaseModel


class DocTableOut(BaseModel):
    index: int
    title: str
    enforcement: str
    columns: list[str]  # Schluessel der Zeilen (deutsch, bleiben fuer Vergleiche)
    # Anzeige der Spalten parallel zu `columns` in der Sprache der Anfrage (0.87.0, ADR 0028)
    labels: list[str] = []
    # Spalten als Text; dazu `_rule` (Regel-ID), `_dir` (in | out) und `_scope` (Art des Bezugs)
    rows: list[dict[str, str | int | list[int] | list[str] | None]]
    # 0.69.0 (Applikations-Doku): ausgeblendete Zeilen mit `_hides` (Begruendung, wer, wann)
    hidden: list[dict] = []
    # 0.101.0: Zonen der Kategorie mit ihren Netzen (Kopf; nicht fuer Kunden-Benutzer)
    zones: list[dict] = []


class SiteRef(BaseModel):
    id: int
    code: str
    name: str
    kind: str = "virtual"  # virtual | physical (ADR 0015)
    physical: list[str] = []  # virtuell: Codes der physischen Standorte
    virtual: list[str] = []  # physisch: Codes der virtuellen Standorte

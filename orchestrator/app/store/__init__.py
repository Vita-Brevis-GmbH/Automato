"""Rule & Service Store - Repository-Schicht.

Grundaufbau: bewusst leer bis auf die Struktur. Das normalisierte Regelmodell
(Ist-Regeln, Adressobjekt-Referenzen, kanonische Service-Objekte, Zonen,
Applikations-Zuordnung) wird in Meilenstein M5 entworfen und hier umgesetzt.

Der Store ist ein eigenes Package, damit er spaeter ohne Architekturaenderung
als eigener Service herausgeloest werden kann (ADR 0003).
"""

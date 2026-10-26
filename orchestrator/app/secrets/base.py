"""Secrets-Provider-Interface (E1.5, ADR 0007 D4).

Adapter-Pattern wie beim SoT-Gateway: ein schmales Interface kapselt die *Herkunft*
eines Secrets. So ist der Bezug (env/.env im Lab, OpenBao/SOPS in Produktion, später
dezentral) austauschbar, ohne dass die konsumierende Seite etwas davon weiss.
"""

from abc import ABC, abstractmethod


class SecretsError(RuntimeError):
    """Secret nicht auflösbar (nicht konfiguriert, nicht gefunden, Backend-Fehler)."""


class SecretProvider(ABC):
    """Löst einen Locator (backend-spezifischer Schlüssel/Pfad) zu einem Wert auf."""

    name: str

    @abstractmethod
    def get(self, locator: str) -> str | None:
        """Gibt den Secret-Wert zurück oder None, wenn es ihn nicht gibt."""

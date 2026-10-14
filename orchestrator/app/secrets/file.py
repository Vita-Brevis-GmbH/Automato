"""File-Provider: Secrets aus Dateien.

Deckt Docker-/K8s-Secrets (`/run/secrets/<name>`) und SOPS/age ab (die entschlüsselte
Datei wird gemountet). Absoluter Locator = direkter Pfad; sonst relativ zum Secrets-
Verzeichnis. Nachgestelltes Newline wird entfernt (Editor/`echo`-Artefakt).
"""

from pathlib import Path

from app.secrets.base import SecretProvider, SecretsError


class FileProvider(SecretProvider):
    name = "file"

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    def get(self, locator: str) -> str | None:
        path = Path(locator)
        if not path.is_absolute():
            path = self._base / locator
        try:
            return path.read_text(encoding="utf-8").rstrip("\n")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SecretsError(f"Secret-Datei '{path}' nicht lesbar: {exc}") from exc

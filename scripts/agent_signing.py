#!/usr/bin/env python3
"""Agent-Updates signieren (Ed25519) - offline, auf einem Rechner ohne Netz zum Orchestrator.

  python3 scripts/agent_signing.py keygen agent-signing.key
      neues Schluesselpaar; gibt den oeffentlichen Schluessel (Base64) aus. Den privaten
      Schluessel offline/sicher verwahren (nie auf den Orchestrator, nie ins Repo).
  python3 scripts/agent_signing.py sign agent-signing.key manifest.txt > agent-package.sig
      signiert die Nachricht von `automato agent-package manifest`.

Danach auf dem Orchestrator: `automato agent-package install-signature agent-package.sig`.
Agenten mit `AUTOMATO_AGENT_SIGNING_PUBKEY=<oeffentlicher Schluessel>` installieren nur noch
Updates mit gueltiger Signatur.
"""

import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CONTEXT = "automato-agent-update/v1"


def _pub(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def keygen(path: str) -> int:
    target = Path(path)
    if target.exists():
        print(f"{path} existiert bereits - nicht ueberschrieben", file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    print(f"privater Schluessel: {path} (offline verwahren)")
    print(f"AUTOMATO_AGENT_SIGNING_PUBKEY={_pub(key)}")
    return 0


def sign(key_path: str, message_path: str) -> int:
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        print("kein Ed25519-Schluessel", file=sys.stderr)
        return 1
    message = Path(message_path).read_bytes()
    lines = message.decode().splitlines()
    if len(lines) < 2 or lines[0] != CONTEXT:
        print("keine Manifest-Nachricht von 'automato agent-package manifest'", file=sys.stderr)
        return 1
    signature = base64.b64encode(key.sign(message)).decode()
    print(json.dumps({"version": lines[1], "signature": signature, "pubkey": _pub(key)}))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "keygen":
        return keygen(argv[1])
    if len(argv) == 3 and argv[0] == "sign":
        return sign(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

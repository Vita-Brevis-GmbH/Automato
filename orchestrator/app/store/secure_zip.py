"""ZIP mit Passwort (1.0.1, Anwender: „Export aller Dokumentationen der Applikationen als mehrere
Zips, welche mit einem Export Passwort verschlüsselt sind“).

AES-256 (WinZip-AES, `pyzipper`) - öffnen mit 7-Zip, WinRAR bzw. `7z x`; der Windows-Explorer
älterer Versionen kennt AES nicht. Das Passwort kommt mit der Anfrage und wird nie gespeichert,
auch nicht im Audit.
"""

import io
import zipfile

import pyzipper

MIN_PASSWORD = 8


def encrypted(files: list[tuple[str, bytes | str]], password: str) -> bytes:
    """ZIP, dessen Dateien mit `password` (AES-256) verschlüsselt sind."""
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES) as z:  # fmt: skip
        z.setpassword(password.encode("utf-8"))
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        for name, body in files:
            z.writestr(name, body.encode() if isinstance(body, str) else body)
    return buf.getvalue()


def unpack(body: bytes) -> list[tuple[str, bytes]]:
    """Dateien eines (unverschlüsselten) ZIPs - für den Gesamt-Export aus dem Einzel-Export."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return [(n, z.read(n)) for n in z.namelist()]


def read(body: bytes, password: str) -> dict[str, bytes]:
    """Inhalt eines verschlüsselten ZIPs (Tests)."""
    with pyzipper.AESZipFile(io.BytesIO(body)) as z:
        z.setpassword(password.encode("utf-8"))
        return {n: z.read(n) for n in z.namelist()}

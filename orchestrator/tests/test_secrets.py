"""Secrets-Provider E1.5 (ADR 0007 D4): Referenz-Auflösung env/file/openbao, Klartext
bleibt unverändert, Settings-Auflösung in-place, fail-closed bei fehlendem Secret."""

import pytest
from app import secrets
from app.config import Settings
from app.secrets import SecretsError
from app.secrets.openbao import OpenBaoProvider


def _settings(**kw) -> Settings:
    return Settings(**kw)


def test_klartext_bleibt_unveraendert():
    s = _settings()
    assert secrets.resolve("ein-klartext-token", s) == "ein-klartext-token"
    # URL mit ':' hat kein bekanntes Schema -> unveraendert
    assert secrets.resolve("https://netbox.example", s) == "https://netbox.example"


def test_literal_escape_hatch():
    s = _settings()
    assert secrets.resolve("literal:wert:mit:doppelpunkt", s) == "wert:mit:doppelpunkt"


def test_env_referenz(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    assert secrets.resolve("env:MY_SECRET", _settings()) == "s3cr3t"


def test_env_referenz_fehlt_ist_fehler():
    with pytest.raises(SecretsError):
        secrets.resolve("env:DOES_NOT_EXIST_XYZ", _settings())


def test_file_referenz(tmp_path):
    f = tmp_path / "netbox_token"
    f.write_text("file-token\n")  # Newline wird entfernt
    s = _settings(secrets_file_dir=str(tmp_path))
    assert secrets.resolve("file:netbox_token", s) == "file-token"
    assert secrets.resolve(f"file:{f}", s) == "file-token"  # absoluter Pfad


def test_file_referenz_fehlt_ist_fehler(tmp_path):
    s = _settings(secrets_file_dir=str(tmp_path))
    with pytest.raises(SecretsError):
        secrets.resolve("file:nope", s)


def test_openbao_referenz(monkeypatch):
    s = _settings(openbao_addr="https://bao:8200", openbao_token="t", openbao_path="automato")
    # HTTP mocken: KV-v2-Dokument
    monkeypatch.setattr(
        OpenBaoProvider,
        "_read",
        lambda self, path: {"data": {"data": {"netbox_token": "bao-token"}}},
    )
    assert secrets.resolve("openbao:netbox_token", s) == "bao-token"
    # explizit pfad#feld
    assert secrets.resolve("openbao:automato#netbox_token", s) == "bao-token"


def test_openbao_unkonfiguriert_ist_fehler():
    s = _settings()  # kein addr/token
    with pytest.raises(SecretsError):
        secrets.resolve("openbao:netbox_token", s)


def test_resolve_settings_loest_nur_secret_felder(monkeypatch):
    monkeypatch.setenv("NB", "aufgeloest")
    s = _settings(netbox_token="env:NB", netbox_url="https://netbox.example")
    secrets.resolve_settings(s)
    assert s.netbox_token == "aufgeloest"  # Secret-Feld aufgeloest
    assert s.netbox_url == "https://netbox.example"  # kein Secret-Feld, unangetastet
    # idempotent: zweiter Lauf laesst den Klartext stehen
    secrets.resolve_settings(s)
    assert s.netbox_token == "aufgeloest"

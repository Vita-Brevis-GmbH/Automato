"""Fail-closed-Prüfung der Sicherheitskonfiguration (ADR 0005)."""

import pytest
from app.config import DEFAULT_INTERNAL_TOKEN, Settings, check_security_config


def test_dev_default_token_ist_erlaubt():
    check_security_config(Settings(environment="dev", internal_api_token=DEFAULT_INTERNAL_TOKEN))


def test_prod_mit_default_token_wird_abgelehnt():
    with pytest.raises(RuntimeError):
        check_security_config(
            Settings(environment="prod", internal_api_token=DEFAULT_INTERNAL_TOKEN)
        )


def test_prod_mit_eigenem_token_ist_erlaubt():
    check_security_config(Settings(environment="prod", internal_api_token="s3cr3t-random"))  # noqa: S106

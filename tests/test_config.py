import pytest
from pydantic import SecretStr, ValidationError

from ado_mcp.config import Settings


def test_settings_require_a_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADO_ORGANIZATION", "contoso")
    monkeypatch.delenv("ADO_PAT", raising=False)
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({"organization": "contoso"})


def test_secret_is_not_visible_in_settings_repr() -> None:
    settings = Settings(
        organization="contoso",
        pat=SecretStr("secret-pat"),
    )

    assert "secret-pat" not in repr(settings)
    assert settings.read_only is True


def test_default_api_version_is_supported_by_azure_devops() -> None:
    settings = Settings(
        organization="contoso",
        pat=SecretStr("secret-pat"),
    )

    assert settings.api_version == "7.1"

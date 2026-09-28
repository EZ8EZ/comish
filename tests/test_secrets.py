import pytest

from commish import secrets


def test_env_overrides_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMISH_WEBHOOK_TOKEN", "from-env")
    assert secrets.get_secret("webhook_token") == "from-env"


def test_missing_secret_raises_with_fix_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMMISH_NOT_A_SECRET", raising=False)
    monkeypatch.setattr(secrets.keyring, "get_password", lambda service, name: None)
    with pytest.raises(secrets.MissingSecret, match="keyring set commish not_a_secret"):
        secrets.get_secret("not_a_secret")

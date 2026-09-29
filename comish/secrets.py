"""Secret lookup: environment variable first, then the macOS Keychain.

Secrets never live in the repo or in .env. On the bot Mac, store them with:

    keyring set comish bluebubbles_password

For tests or one-off runs, COMISH_<NAME> in the environment overrides the Keychain.
"""

import os

import keyring

KEYRING_SERVICE = "comish"


class MissingSecret(RuntimeError):
    pass


def get_secret(name: str) -> str:
    value = os.environ.get(f"COMISH_{name.upper()}")
    if not value:
        try:
            value = keyring.get_password(KEYRING_SERVICE, name)
        except keyring.errors.KeyringError:
            value = None
    if not value:
        raise MissingSecret(f"Secret '{name}' not found. Run: keyring set {KEYRING_SERVICE} {name}")
    return value


def optional_secret(name: str) -> str | None:
    try:
        return get_secret(name)
    except MissingSecret:
        return None

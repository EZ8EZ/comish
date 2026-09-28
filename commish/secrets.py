"""Secret lookup: environment variable first, then the macOS Keychain.

Secrets never live in the repo or in .env. On the bot Mac, store them with:

    keyring set commish bluebubbles_password

For tests or one-off runs, COMMISH_<NAME> in the environment overrides the Keychain.
"""

import os

import keyring

KEYRING_SERVICE = "commish"


class MissingSecret(RuntimeError):
    pass


def get_secret(name: str) -> str:
    value = os.environ.get(f"COMMISH_{name.upper()}")
    if not value:
        try:
            value = keyring.get_password(KEYRING_SERVICE, name)
        except keyring.errors.KeyringError:
            value = None
    if not value:
        raise MissingSecret(f"Secret '{name}' not found. Run: keyring set {KEYRING_SERVICE} {name}")
    return value

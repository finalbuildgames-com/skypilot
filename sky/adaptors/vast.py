"""Vast cloud adaptor."""

import functools
import os
from typing import Optional

_vast_sdk = None
_vast_sdk_key: Optional[str] = None

_CONFIG_DIR = '~/.config/vastai'
_API_KEY_FILE = f'{_CONFIG_DIR}/vast_api_key'
# `vastai tfa login` writes the key of a two-factor session here. The vastai
# CLI prefers it over the API key; an account with two-factor authentication
# turned on refuses every call made with the plain API key (HTTP 401).
TFA_KEY_FILE = f'{_CONFIG_DIR}/vast_tfa_key'


def _read(path: str) -> Optional[str]:
    try:
        with open(os.path.expanduser(path), 'r', encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def get_api_key() -> Optional[str]:
    """The key to call Vast with: $VAST_API_KEY, else the two-factor session
    key if there is one, else the API key (the order the vastai CLI uses)."""
    return (os.environ.get('VAST_API_KEY') or _read(TFA_KEY_FILE) or
            _read(_API_KEY_FILE))


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _vast_sdk, _vast_sdk_key

        key = get_api_key()
        # A long-lived process (the API server) must pick up a refreshed
        # session key, so the client is rebuilt when the key changes.
        if _vast_sdk is None or key != _vast_sdk_key:
            try:
                import vastai_sdk as _vast  # pylint: disable=import-outside-toplevel
                _vast_sdk = _vast.VastAI(api_key=key) if key else _vast.VastAI()
                _vast_sdk_key = key
            except ImportError as e:
                raise ImportError(f'Fail to import dependencies for vast: {e}\n'
                                  'Try pip install "skypilot[vast]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def vast():
    """Return the vast package."""
    return _vast_sdk

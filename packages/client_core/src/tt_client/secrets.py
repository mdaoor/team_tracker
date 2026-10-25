"""Where secrets live on a device: the OS keyring first, a 0600 file only when there is no keyring (headless boxes, CI).

Used for the personal-store encryption key and for calendar OAuth tokens (Phase 15). Nothing here ever writes a secret into the
outbox, a log line or the config file. The file fallback prints one warning per process so nobody believes they have a keychain.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("tt_client.secrets")
SERVICE = "team-tracker"
_WARNED: set[str] = set()


class SecretStore(Protocol):
    """A tiny name -> secret map. `name` is namespaced by the caller (e.g. `personal-store-key`, `oauth:google`)."""

    kind: str

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def names(self) -> list[str]: ...


class KeyringStore:
    """The OS keychain through the `keyring` package (macOS Keychain, Windows Credential Manager, libsecret).

    `keyring` cannot enumerate, so the names we have written are kept in one extra entry (`__index__`).
    """

    kind = "keyring"

    def __init__(self, backend: Any = None) -> None:
        import keyring

        self._kr: Any = backend or keyring

    def _index(self) -> list[str]:
        raw = self._kr.get_password(SERVICE, "__index__")
        return list(json.loads(raw)) if raw else []

    def _save_index(self, names: list[str]) -> None:
        self._kr.set_password(SERVICE, "__index__", json.dumps(sorted(set(names))))

    def get(self, name: str) -> str | None:
        value = self._kr.get_password(SERVICE, name)
        return str(value) if value is not None else None

    def set(self, name: str, value: str) -> None:
        self._kr.set_password(SERVICE, name, value)
        self._save_index([*self._index(), name])

    def delete(self, name: str) -> None:
        try:
            self._kr.delete_password(SERVICE, name)
        except Exception:  # noqa: S110 - deleting something that is not there is fine
            pass
        self._save_index([n for n in self._index() if n != name])

    def names(self) -> list[str]:
        return self._index()


class FileStore:
    """`secrets.json` next to the data, mode 0600. For tests and headless machines only; warns once."""

    kind = "file"

    def __init__(self, path: Path, *, warn: bool = True) -> None:
        self.path = path
        if warn and str(path) not in _WARNED:
            _WARNED.add(str(path))
            log.warning(
                "no OS keyring available: secrets are kept in %s (mode 0600). Use a desktop session for the keychain.",
                path,
            )

    def _read(self) -> dict[str, str]:
        try:
            return dict(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data))
        self.path.chmod(0o600)

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        data = self._read()
        data[name] = value
        self._write(data)

    def delete(self, name: str) -> None:
        data = self._read()
        if data.pop(name, None) is not None:
            self._write(data)

    def names(self) -> list[str]:
        return sorted(self._read())


class MemoryStore:
    """Process-local, for unit tests."""

    kind = "memory"

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self.data)


def keyring_usable() -> bool:
    """True when `keyring` has a real backend (not the `fail`/`null` placeholders)."""
    try:
        import keyring
        from keyring.backends import chainer, fail

        backend = keyring.get_keyring()
        if isinstance(backend, chainer.ChainerBackend):
            return any(not isinstance(b, fail.Keyring) for b in backend.backends)
        return not isinstance(backend, fail.Keyring) and backend.priority > 0
    except Exception:
        return False


def default_store(data_dir: Path) -> SecretStore:
    """`TT_SECRETS=file|keyring` forces a choice; otherwise the keyring when usable, else the 0600 file."""
    forced = os.environ.get("TT_SECRETS", "").lower()
    if forced == "file" or (forced != "keyring" and not keyring_usable()):
        return FileStore(data_dir / "secrets.json")
    return KeyringStore()

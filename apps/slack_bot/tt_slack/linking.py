"""Account linking: a Slack user id maps to one Team Tracker handle + token. Tokens live only in this file (mode 0600), never in Slack.

Flow: `/tt link` -> the bot DMs a deep link `<server>/slack/link?state=<signed>`; the web login (any method) then calls
`LinkStore.complete(state, handle, token)` through the bot's callback. State is HMAC-signed and expires."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

STATE_TTL_S = 600


@dataclass
class Link:
    handle: str
    token: str
    workspace: str


class LinkError(Exception):
    pass


class LinkStore:
    def __init__(self, path: Path, secret: str) -> None:
        self.path, self.secret = path, secret.encode()

    def _read(self) -> dict[str, dict[str, str]]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}  # type: ignore[no-any-return]

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def get(self, slack_user: str) -> Link | None:
        d = self._read().get(slack_user)
        return Link(**d) if d else None

    def unlink(self, slack_user: str) -> bool:
        data = self._read()
        gone = data.pop(slack_user, None) is not None
        self._write(data)
        return gone

    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()[:32]

    def begin(self, slack_user: str, *, now: float | None = None) -> str:
        payload = f"{slack_user}.{int(now or time.time())}"
        return f"{payload}.{self._sign(payload)}"

    def deep_link(self, server_url: str, slack_user: str, *, now: float | None = None) -> str:
        return f"{server_url.rstrip('/')}/slack/link?state={self.begin(slack_user, now=now)}"

    def complete(
        self, state: str, handle: str, token: str, workspace: str, *, now: float | None = None
    ) -> str:
        try:
            user, ts, sig = state.rsplit(".", 2)
            issued = int(ts)
        except ValueError as exc:
            raise LinkError("malformed link state") from exc
        if not hmac.compare_digest(sig, self._sign(f"{user}.{ts}")):
            raise LinkError("bad signature")
        if (now or time.time()) - issued > STATE_TTL_S:
            raise LinkError("this link expired; run /tt link again")
        data = self._read()
        data[user] = {"handle": handle, "token": token, "workspace": workspace}
        self._write(data)
        return user

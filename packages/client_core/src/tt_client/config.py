"""Client configuration (`~/.config/tt/config.toml`) and data directories."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    return Path(os.environ.get("TT_CONFIG_DIR") or Path.home() / ".config" / "tt")


def data_dir() -> Path:
    return Path(os.environ.get("TT_DATA_DIR") or Path.home() / ".local" / "share" / "tt")


@dataclass
class ClientConfig:
    server_url: str = "http://localhost:8000"
    workspace: str = ""
    handle: str = ""
    token: str = ""
    ca_bundle: str | None = None  # PEM bundle for internal CAs (TT_CA_BUNDLE)
    gateway_url: str | None = None
    llm_mode: str = "translate"  # translate | agent
    llm_timeout: float = 1.5
    llm_agent_timeout: float = (
        20.0  # the agent loop makes several model + tool calls (p95 budget 6 s): 1.5 s always fell back
    )
    default_mode: str = "assisted"  # assisted | manual
    feedback: bool = False
    time_zone: str = "UTC"
    hours_per_day: int = 8
    jira_default: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> ClientConfig:
        path = path or config_dir() / "config.toml"
        data: dict[str, Any] = tomllib.loads(path.read_text()) if path.exists() else {}
        server, llm, client = data.get("server", {}), data.get("llm", {}), data.get("client", {})
        return cls(
            server_url=server.get("url", cls.server_url),
            workspace=server.get("workspace", ""),
            handle=server.get("handle", ""),
            token=os.environ.get("TT_TOKEN") or server.get("token", ""),
            ca_bundle=os.environ.get("TT_CA_BUNDLE") or server.get("ca_bundle"),
            gateway_url=llm.get("gateway_url"),
            llm_mode=llm.get("mode", "translate"),
            llm_timeout=float(llm.get("timeout", 1.5)),
            llm_agent_timeout=float(llm.get("agent_timeout", 20.0)),
            default_mode=client.get("default_mode", "assisted"),
            feedback=bool(client.get("feedback", False)),
            time_zone=client.get("time_zone", "UTC"),
            hours_per_day=int(client.get("hours_per_day", 8)),
            jira_default=bool(client.get("jira_default", False)),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)

        def q(v: str) -> str:
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

        lines = [
            "[server]",
            f"url = {q(self.server_url)}",
            f"workspace = {q(self.workspace)}",
            f"handle = {q(self.handle)}",
        ]
        if self.token:
            lines.append(f"token = {q(self.token)}")
        if self.ca_bundle:
            lines.append(f"ca_bundle = {q(self.ca_bundle)}")
        lines += [
            "",
            "[llm]",
            f"mode = {q(self.llm_mode)}",
            f"timeout = {self.llm_timeout}",
            f"agent_timeout = {self.llm_agent_timeout}",
        ]
        if self.gateway_url:
            lines.append(f"gateway_url = {q(self.gateway_url)}")
        lines += [
            "",
            "[client]",
            f"default_mode = {q(self.default_mode)}",
            f"feedback = {str(self.feedback).lower()}",
            f"time_zone = {q(self.time_zone)}",
            f"hours_per_day = {self.hours_per_day}",
            f"jira_default = {str(self.jira_default).lower()}",
        ]
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)  # contains a token
        return path

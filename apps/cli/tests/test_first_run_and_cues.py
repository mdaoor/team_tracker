"""Phase 13 R6.7 and UX issue 15 end to end through the real `tt` app: first-run ghost examples, the empty state, the end-of-day
confirm nudge (through the NotificationPort), and the CLI cues being honest (no failure copy when nothing failed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tt_cli.main import app
from tt_cli.runtime import GHOST_EXAMPLES
from tt_client.api import TTApi
from tt_client.catalogue import Catalogue
from tt_client.config import ClientConfig
from tt_client.testing.fake_command_server import FakeServer
from typer.testing import CliRunner

runner = CliRunner()
CATALOGUE = Path(__file__).parents[3] / "packages" / "client_core" / "tests" / "fixtures" / "catalogue.json"
EVENING = "2026-10-23T15:00:00+00:00"  # 18:00 in Riyadh: after the 17:30 daily-log time
MORNING = "2026-10-23T05:00:00+00:00"  # 08:00 in Riyadh
PACK: dict[str, Any] = {
    "context_schema_version": 1,
    "actor": "@mahmoud",
    "workspace": "acme",
    "projects": [{"key": "WEB", "name": "Website", "aliases": ["site"]}],
    "my_open_items": [{"key": "WEB-114", "title": "Checkout timeouts", "project": "WEB"}],
    "people": [],
    "tags": [],
    "manual_mode": {"required_fields": ["action", "item", "project"]},
}


class Backend:
    """FakeServer plus the two daily-log endpoints, so `tt today` and the nudge run over real HTTP."""

    def __init__(self) -> None:
        self.server = FakeServer(Catalogue.from_doc(json.loads(CATALOGUE.read_text())), pack=PACK)
        self.log: dict[str, Any] | None = None
        self.confirmed: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/w/acme", 1)[-1]
        if request.method == "GET" and path == "/daily-logs":
            return httpx.Response(200, json={"data": [self.log] if self.log else []})
        if request.method == "POST" and path.endswith("/confirm") and self.log:
            self.confirmed.append(self.log["id"])
            self.log = {**self.log, "status": "confirmed"}
            return httpx.Response(200, json=self.log)
        return self.server.handle(request)


def draft_log() -> dict[str, Any]:
    return {
        "id": "dl-1",
        "date": "2026-10-23",
        "status": "draft",
        "minutes_total": 270,
        "summary": "Checkout timeouts, 4.5 h",
        "blockers": None,
    }


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Backend:
    b = Backend()
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TT_NOW", EVENING)
    monkeypatch.delenv("TT_TOKEN", raising=False)
    ClientConfig(
        server_url="http://tt", workspace="acme", handle="@mahmoud", token="t", time_zone="Asia/Riyadh"
    ).save(tmp_path / "cfg" / "config.toml")

    def make_api(cfg: ClientConfig, **kw: Any) -> TTApi:
        return TTApi(cfg, client=httpx.Client(transport=httpx.MockTransport(b.handle), base_url="http://tt"))

    monkeypatch.setattr("tt_cli.runtime.TTApi", make_api)
    return b


def tt(*args: str, code: int = 0) -> str:
    res = runner.invoke(app, list(args))
    assert res.exit_code == code, f"tt {' '.join(args)} -> {res.exit_code}\n{res.output}"
    return res.output


# ---------------------------------------------------------------- first run and the empty state


def test_first_run_with_no_login_shows_ghost_examples_and_how_to_log_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "nocfg"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "nodata"))
    monkeypatch.delenv("TT_TOKEN", raising=False)
    out = tt("fix the login redirect", code=2)
    assert "Not logged in" in out and "tt login" in out
    for example in GHOST_EXAMPLES:
        assert f'tt "{example}"' in out
    assert not list((tmp_path / "nodata").glob("outbox/*.json"))  # nothing was captured


def test_an_empty_line_asks_the_one_question_and_shows_examples(backend: Backend) -> None:
    out = tt("say", " ")
    assert "What are you working on?" in out
    for example in GHOST_EXAMPLES:
        assert f'tt "{example}"' in out
    assert not backend.server.requests or all(
        m != "POST" for m, _ in backend.server.requests
    )  # nothing was sent


def test_the_empty_state_mentions_what_is_running_now(backend: Backend) -> None:
    backend.server.pack = {**PACK, "my_current_activity": {"key": "WEB-114", "title": "Checkout timeouts"}}
    out = tt("say", " ")
    assert "now running: WEB-114 Checkout timeouts" in out and "tt done" in out


# ---------------------------------------------------------------- end of day confirm through the NotificationPort


def test_after_the_log_time_a_capture_asks_once_to_confirm_the_draft(backend: Backend) -> None:
    backend.log = draft_log()
    first = tt("note", "WEB-114 still timing out", "--yes")
    assert "Confirm today's log?" in first and "4.5 h" in first and "(tt today --confirm)" in first
    second = tt("note", "WEB-114 and again", "--yes")
    assert "Confirm today's log?" not in second  # once per local day


def test_no_nudge_in_the_morning_or_without_a_draft_or_once_confirmed(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend.log = draft_log()
    monkeypatch.setenv("TT_NOW", MORNING)
    assert "Confirm today's log?" not in tt("note", "WEB-114 early", "--yes")
    monkeypatch.setenv("TT_NOW", EVENING)
    backend.log = None
    assert "Confirm today's log?" not in tt("note", "WEB-114 no draft", "--yes")
    backend.log = {**draft_log(), "status": "confirmed"}
    assert "Confirm today's log?" not in tt("note", "WEB-114 confirmed", "--yes")


def test_the_nudge_action_works_and_the_server_is_not_asked_twice(backend: Backend) -> None:
    backend.log = draft_log()
    assert "Confirm today's log?" in tt("note", "WEB-114 slow", "--yes")
    shown = tt("today")
    assert "draft" in shown and "4 h 30 min" in shown and backend.confirmed == []
    assert "Confirmed your day: 4 h 30 min." in tt("today", "--confirm")
    assert backend.confirmed == ["dl-1"] and backend.log["status"] == "confirmed"


def test_a_down_server_never_breaks_the_command_that_would_nudge(backend: Backend) -> None:
    backend.server.offline = True
    out = tt("note", "WEB-114 offline", "--yes")
    assert "offline" in out and "Confirm today's log?" not in out


# ---------------------------------------------------------------- UX issue 15: honest cues


def test_blocked_on_a_matched_task_does_not_say_the_manager_will_sort_the_project(backend: Backend) -> None:
    out = tt("blocked", "WEB-114 waiting for ops", "--no-send")
    assert "WEB-114" in out and "Project" in out and "WEB" in out
    assert "sorted by your manager" not in out and "LLM offline" not in out


def test_the_manager_cue_only_shows_when_the_project_really_is_unresolved(backend: Backend) -> None:
    out = tt("say", "a stray thought about nothing in particular here", "--no-send")
    assert "will be sorted by your manager" in out


def test_llm_offline_is_said_only_when_the_gateway_was_tried(
    backend: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "LLM offline" not in tt("blocked", "WEB-114 waiting for ops", "--no-send")  # no gateway configured
    ClientConfig(
        server_url="http://tt",
        workspace="acme",
        handle="@mahmoud",
        token="t",
        time_zone="Asia/Riyadh",
        gateway_url="http://127.0.0.1:9",  # nothing listens: the model really is unavailable
    ).save(tmp_path / "cfg" / "config.toml")
    out = tt("blocked", "WEB-114 waiting for ops", "--no-send")
    assert "LLM offline — parsed locally" in out


def test_today_with_no_log_does_not_promise_a_draft_that_is_not_there(backend: Backend) -> None:
    out = tt("today")
    assert "nothing logged today yet" in out and "17:30" not in out and "drafted at" not in out
    backend.log = draft_log()
    assert "Checkout timeouts, 4.5 h" in tt("today")


def test_yes_in_a_pipe_says_that_nothing_was_asked(backend: Backend) -> None:
    out = tt("note", "WEB-114 still slow")  # CliRunner stdin is not a terminal
    assert "not a terminal, so nothing was asked" in out


# ---------------------------------------------------------------- PR #11 review: the day the server drafts, `tt today`, `tt eod --confirm`
RECORDED_LOG = json.loads(
    (
        Path(__file__).parents[3]
        / "packages"
        / "client_core"
        / "tests"
        / "fixtures"
        / "server"
        / "daily-logs.json"
    ).read_text()
)["data"][0]


@pytest.fixture
def personal_api(backend: Backend, monkeypatch: pytest.MonkeyPatch) -> Backend:
    """`tt eod` builds its own TTApi (the personal tracker has no Runtime): point it at the same fake backend."""

    def make_api(cfg: ClientConfig, **kw: Any) -> TTApi:
        return TTApi(
            cfg, client=httpx.Client(transport=httpx.MockTransport(backend.handle), base_url="http://tt")
        )

    monkeypatch.setattr("tt_client.api.TTApi", make_api)
    monkeypatch.setenv("TT_SECRETS", "file")
    return backend


def _sent_a_work_item_today() -> None:
    ref = tt("add", "work: fix the login redirect loop 45m p:WEB").split()[-1]
    tt("item", "done", ref)
    tt("eod", "--send-all")


def test_today_shows_the_draft_the_server_made_as_soon_as_time_was_logged(backend: Backend) -> None:
    backend.log = RECORDED_LOG  # a real DailyLog row, recorded from the in-process server
    out = tt("today")
    assert "2026-10-23" in out and "draft" in out and "45 min" in out and "WEB-136" in out
    assert "nothing logged today yet" not in out and "{" not in out


def test_today_confirm_prints_one_sentence_not_json(backend: Backend) -> None:
    backend.log = RECORDED_LOG
    out = tt("today", "--confirm")
    assert "Confirmed your day: 45 min on WEB-136." in out and "{" not in out
    assert backend.confirmed == [RECORDED_LOG["id"]]


def test_eod_confirm_confirms_the_server_draft_and_says_so_in_one_sentence(personal_api: Backend) -> None:
    personal_api.log = RECORDED_LOG
    _sent_a_work_item_today()
    out = tt("eod", "--confirm")
    assert "Confirmed your day: 45 min on WEB-136." in out
    assert "{" not in out and 'confirmed":' not in out  # never raw JSON
    assert personal_api.confirmed == [RECORDED_LOG["id"]]


def test_eod_confirm_without_a_draft_explains_in_words(personal_api: Backend) -> None:
    personal_api.log = None
    _sent_a_work_item_today()
    out = tt("eod", "--confirm")
    assert "{" not in out and "no draft" in out.lower() and personal_api.confirmed == []


def test_eod_confirm_confirms_work_logged_through_capture_even_if_nothing_was_sent_from_the_planner(
    personal_api: Backend,
) -> None:
    """The draft exists because time was reported (`tt say ... took 45m`); the planner's own 'sent' list is empty."""
    personal_api.log = RECORDED_LOG
    out = tt("eod", "--confirm")
    assert "Confirmed your day: 45 min on WEB-136." in out and "nothing was sent" not in out
    assert personal_api.confirmed == [RECORDED_LOG["id"]]


def test_eod_confirm_with_nothing_sent_today_says_why(personal_api: Backend) -> None:
    out = tt("eod", "--confirm")
    assert "{" not in out and "nothing was sent today" in out

"""axi: XPLAN_ALLOWED_USERS drops the browser toolset for everyone else.

On an axi deployment the browser toolset drives ONE logged-in XPLAN session.
Open WebUI names the chatting user in X-Axi-Agent-User; contact-layer, which
gates planners itself, says so in X-Axi-Agent-Caller. Anyone else — including
a request that names nobody — gets no browser.
"""
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.axi_xplan_gate import browser_allowed, filter_toolsets, remember_request
from tests.gateway.test_api_server import _create_app, _make_adapter

TOOLSETS = ["axi", "browser", "web", "memory"]


@pytest.fixture
def restricted(monkeypatch):
    monkeypatch.setenv("XPLAN_ALLOWED_USERS", "owner@example.com, second@example.com")


# --- the rule ----------------------------------------------------------------


def test_unset_list_keeps_the_browser(monkeypatch):
    monkeypatch.delenv("XPLAN_ALLOWED_USERS", raising=False)
    remember_request({})
    assert browser_allowed()
    assert filter_toolsets(TOOLSETS) == TOOLSETS


def test_listed_user_keeps_the_browser_case_insensitively(restricted):
    remember_request({"X-Axi-Agent-User": "Owner@Example.com"})
    assert filter_toolsets(TOOLSETS) == TOOLSETS


def test_unlisted_user_loses_only_the_browser(restricted):
    remember_request({"X-Axi-Agent-User": "someone@example.com"})
    assert filter_toolsets(TOOLSETS) == ["axi", "web", "memory"]


def test_a_request_naming_nobody_loses_the_browser(restricted):
    # Fail closed: if Open WebUI stops sending the header, nobody drives XPLAN.
    remember_request({})
    assert not browser_allowed()


def test_contact_layer_keeps_the_browser(restricted):
    remember_request({"X-Axi-Agent-Caller": "contact-layer"})
    assert browser_allowed()


def test_any_other_caller_value_does_not(restricted):
    remember_request({"X-Axi-Agent-Caller": "open-webui"})
    assert not browser_allowed()


# --- wired through the API server ---------------------------------------------


@pytest.fixture
def captured_toolsets(monkeypatch):
    """Run the real _create_agent with a fake AIAgent that records its toolsets."""
    captured = {}

    class FakeAgent:
        session_prompt_tokens = session_completion_tokens = session_total_tokens = 0

        def __init__(self, **kwargs):
            captured["toolsets"] = kwargs["enabled_toolsets"]

        def run_conversation(self, **kwargs):
            return {"final_response": "ok", "messages": []}

        def __getattr__(self, name):
            return MagicMock()

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "m")
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    monkeypatch.setattr("gateway.run.GatewayRunner._load_reasoning_config", staticmethod(lambda: None))
    monkeypatch.setattr("gateway.run.GatewayRunner._load_fallback_model", staticmethod(lambda: None))
    monkeypatch.setattr("hermes_cli.tools_config._get_platform_tools", lambda *_: set(TOOLSETS))
    return captured


async def _chat(headers):
    adapter = _make_adapter(api_key="sk-secret")
    adapter._ensure_session_db = lambda: None
    async with TestClient(TestServer(_create_app(adapter))) as cli:
        resp = await cli.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-secret", **headers},
            json={"model": "hermes-agent", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
async def test_chat_from_an_unlisted_user_runs_without_the_browser(restricted, captured_toolsets):
    await _chat({"X-Axi-Agent-User": "someone@example.com"})
    assert "browser" not in captured_toolsets["toolsets"]
    assert "axi" in captured_toolsets["toolsets"]


@pytest.mark.asyncio
async def test_chat_from_a_listed_user_keeps_the_browser(restricted, captured_toolsets):
    await _chat({"X-Axi-Agent-User": "owner@example.com"})
    assert "browser" in captured_toolsets["toolsets"]


@pytest.mark.asyncio
async def test_streamed_chat_from_an_unlisted_user_runs_without_the_browser(restricted, captured_toolsets):
    # Streaming runs the agent in a separate task and thread; the identity must
    # still reach it.
    adapter = _make_adapter(api_key="sk-secret")
    adapter._ensure_session_db = lambda: None
    async with TestClient(TestServer(_create_app(adapter))) as cli:
        resp = await cli.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-secret", "X-Axi-Agent-User": "someone@example.com"},
            json={"model": "hermes-agent", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        await resp.read()
    assert "browser" not in captured_toolsets["toolsets"]

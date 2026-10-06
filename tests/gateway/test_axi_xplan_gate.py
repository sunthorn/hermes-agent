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


# --- enforced where the browser is reached, not only in the toolset list ------


def test_an_unlisted_user_never_gets_the_shared_browser_address(restricted, monkeypatch):
    # Composite toolsets (coding, hermes-api-server, hermes-acp) carry the
    # browser tools too, so dropping the `browser` toolset alone is not enough.
    # The CDP address itself must be withheld: without it the tools fall back
    # to the in-container headless browser, which has no XPLAN session.
    from tools import browser_tool
    monkeypatch.setenv("BROWSER_CDP_URL", "http://192.168.65.254:9222")
    monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda url: url)
    remember_request({"X-Axi-Agent-User": "someone@example.com"})
    assert browser_tool._get_cdp_override() == ""


def test_a_listed_user_still_gets_it(restricted, monkeypatch):
    from tools import browser_tool
    monkeypatch.setenv("BROWSER_CDP_URL", "http://192.168.65.254:9222")
    monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda url: url)
    remember_request({"X-Axi-Agent-User": "owner@example.com"})
    assert browser_tool._get_cdp_override() == "http://192.168.65.254:9222"


# --- a cached browser session is not a way around the gate --------------------


def test_two_users_with_the_same_opening_message_get_different_sessions(restricted, monkeypatch):
    # hermes derives a chat's session id from its first message. Without the
    # user in the seed, two planners who both open with "hi" share a session —
    # and its cached browser.
    from gateway.platforms import api_server
    from gateway.axi_xplan_gate import session_scope

    remember_request({"X-Axi-Agent-User": "owner@example.com"})
    mine = api_server._derive_chat_session_id("sys", "hi", session_scope())
    remember_request({"X-Axi-Agent-User": "someone@example.com"})
    theirs = api_server._derive_chat_session_id("sys", "hi", session_scope())
    assert mine != theirs
    # no axi identity: unchanged from upstream
    assert api_server._derive_chat_session_id("sys", "hi") == api_server._derive_chat_session_id("sys", "hi", "")


@pytest.fixture
def no_real_browser(monkeypatch):
    from tools import browser_tool
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
    monkeypatch.setattr(browser_tool, "_create_local_session",
                        lambda task_id: {"session_name": f"local-{task_id}", "cdp_url": None})
    monkeypatch.setattr(browser_tool, "_active_sessions", {})
    return browser_tool


def test_an_unlisted_user_does_not_inherit_a_cached_shared_browser_session(restricted, no_real_browser):
    bt = no_real_browser
    bt._active_sessions["t1"] = {"session_name": "cdp-t1", "cdp_url": "ws://192.168.65.254:9222/devtools/browser/x"}
    remember_request({"X-Axi-Agent-User": "someone@example.com"})
    info = bt._get_session_info("t1")
    assert not info.get("cdp_url")


def test_a_listed_user_keeps_their_cached_session(restricted, no_real_browser):
    bt = no_real_browser
    cached = {"session_name": "cdp-t1", "cdp_url": "ws://192.168.65.254:9222/devtools/browser/x"}
    bt._active_sessions["t1"] = cached
    remember_request({"X-Axi-Agent-User": "owner@example.com"})
    assert bt._get_session_info("t1") is cached


def test_the_supervisor_never_attaches_to_a_cached_shared_browser_for_an_unlisted_user(
        restricted, no_real_browser, monkeypatch):
    bt = no_real_browser
    from tools import browser_supervisor
    started = []
    monkeypatch.setattr(browser_supervisor.SUPERVISOR_REGISTRY, "get_or_start",
                        lambda **kw: started.append(kw))
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    bt._active_sessions["t1"] = {"session_name": "cdp-t1", "cdp_url": "ws://192.168.65.254:9222/devtools/browser/x"}
    remember_request({"X-Axi-Agent-User": "someone@example.com"})
    bt._ensure_cdp_supervisor("t1")
    assert started == []


# --- per-owner CDP proxy URL ---------------------------------------------------

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor

from gateway.axi_xplan_gate import cdp_url
from tools.thread_context import propagate_context_to_thread


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.delenv("XPLAN_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("XPLAN_CDP_PROXY", "http://contact-layer:8200/cdp")
    monkeypatch.setenv("XPLAN_CDP_SECRET", "s3cret")


def _sig(owner):
    return hmac.new(b"s3cret", owner.encode(), hashlib.sha256).hexdigest()[:32]


def test_cdp_url_names_the_chatting_owner(proxied):
    remember_request({"X-Axi-Agent-Owner": "owner-1"})
    assert cdp_url() == f"http://contact-layer:8200/cdp/owner-1/{_sig('owner-1')}"


def test_no_owner_no_url(proxied):
    remember_request({})
    assert cdp_url() == ""


def test_malformed_owner_no_url(proxied):
    remember_request({"X-Axi-Agent-Owner": "../owner-1"})
    assert cdp_url() == ""


def test_no_proxy_configured_no_url(monkeypatch):
    monkeypatch.delenv("XPLAN_CDP_PROXY", raising=False)
    remember_request({"X-Axi-Agent-Owner": "owner-1"})
    assert cdp_url() == ""


def test_not_allowed_no_url(proxied, monkeypatch):
    monkeypatch.setenv("XPLAN_ALLOWED_USERS", "owner@example.com")
    remember_request({"X-Axi-Agent-Owner": "owner-1", "X-Axi-Agent-User": "else@example.com"})
    assert cdp_url() == ""


def test_owner_reaches_tool_worker_threads(proxied):
    remember_request({"X-Axi-Agent-Owner": "owner-1"})
    with ThreadPoolExecutor(1) as pool:
        got = pool.submit(propagate_context_to_thread(cdp_url)).result()
    assert "/owner-1/" in got


def test_browser_tool_uses_the_chat_owners_url(proxied, monkeypatch):
    from tools import browser_tool
    monkeypatch.setenv("BROWSER_CDP_URL", "http://192.168.65.254:9222")
    monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda url: f"resolved:{url}")
    remember_request({"X-Axi-Agent-Owner": "owner-1"})
    assert browser_tool._get_cdp_override() == (
        f"resolved:http://contact-layer:8200/cdp/owner-1/{_sig('owner-1')}")


def test_proxy_mode_never_falls_back_to_a_shared_browser(proxied, monkeypatch):
    from tools import browser_tool
    monkeypatch.setenv("BROWSER_CDP_URL", "http://192.168.65.254:9222")
    remember_request({})
    assert browser_tool._get_cdp_override() == ""

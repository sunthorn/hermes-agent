"""axi: who may use the browser toolset (XPLAN_ALLOWED_USERS).

On an axi deployment the browser toolset drives ONE logged-in XPLAN session,
whoever signed in to the debug Chrome. Every other planner chatting with the
agent would act as that person in XPLAN, so a deployment can name the accounts
that may keep the toolset. Unset means unrestricted.

Identity comes from headers only the API server's callers can set (they hold
API_SERVER_KEY):
  X-Axi-Agent-User    the chatting user's email; Open WebUI fills it per
                      connection from {{USER_EMAIL}}
  X-Axi-Agent-Owner   the chatting user's id ({{USER_ID}}); picks their browser
  X-Axi-Agent-Caller  "contact-layer" for its syncs and jobs, which gate the
                      planner themselves and may run with no planner at all
A request carrying neither loses the browser: fail closed.

This decides which toolsets an agent is built with; it verifies nothing.
Authentication stays with security-layer and the API key.
"""
import hashlib
import hmac
import os
import re
from contextvars import ContextVar
from typing import Iterable, List, Mapping

USER_HEADER = "X-Axi-Agent-User"
CALLER_HEADER = "X-Axi-Agent-Caller"
OWNER_HEADER = "X-Axi-Agent-Owner"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TRUSTED_CALLER = "contact-layer"
BROWSER_TOOLSET = "browser"

# (user email, caller, owner id) of the request being served. A ContextVar so it follows
# the request into the tasks it spawns; the API server copies it into the
# executor thread that builds the agent.
_identity: ContextVar[tuple] = ContextVar("axi_xplan_identity", default=("", "", ""))


def remember_request(headers: Mapping[str, str]) -> None:
    """Record who is asking, for the agent this request builds."""
    _identity.set((
        (headers.get(USER_HEADER) or "").strip().lower(),
        (headers.get(CALLER_HEADER) or "").strip(),
        (headers.get(OWNER_HEADER) or "").strip(),
    ))


def _allowed_users() -> frozenset:
    raw = os.getenv("XPLAN_ALLOWED_USERS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def browser_allowed() -> bool:
    allowed = _allowed_users()
    if not allowed:
        return True
    user, caller, _ = _identity.get()
    return caller == TRUSTED_CALLER or (bool(user) and user in allowed)


def filter_toolsets(toolsets: Iterable[str]) -> List[str]:
    toolsets = list(toolsets)
    if browser_allowed():
        return toolsets
    return [t for t in toolsets if t != BROWSER_TOOLSET]


def session_scope() -> str:
    """Who this chat belongs to, for keying per-chat state.

    hermes derives a chat's session id from its opening message; two planners
    who open with the same words would otherwise share a session, and with it
    a cached browser. Empty when the request names nobody.
    """
    return _identity.get()[0]


def cdp_url() -> str:
    """The CDP proxy URL for the planner this chat belongs to, or "".

    contact-layer serves each planner's own browser at
    <XPLAN_CDP_PROXY>/<owner>/<sig> (shared-contracts/xplan-browser-spec.md §5).
    """
    proxy = os.getenv("XPLAN_CDP_PROXY", "").strip()
    secret = os.getenv("XPLAN_CDP_SECRET", "")
    owner = _identity.get()[2]
    if not proxy or not secret or not _OWNER_RE.fullmatch(owner) or not browser_allowed():
        return ""
    sig = hmac.new(secret.encode(), owner.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{proxy.rstrip('/')}/{owner}/{sig}"

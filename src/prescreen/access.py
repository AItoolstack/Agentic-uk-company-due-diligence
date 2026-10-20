"""
Who is calling, and what they may do.

Three kinds of caller on a shared deployment:

  owner   sends PRESCREEN_ACCESS_TOKEN. Everything, and agent reviews on AGENT_PROVIDER (which may be paid).
  guest   sends a demo code listed in PRESCREEN_GUEST_CODES. Runs live pre-screens and agent reviews on free
          models only, within daily allowances, and sees only their own cases plus the examples.
  public  sends nothing, and only when PRESCREEN_PUBLIC_EXAMPLES is set: reads the saved example cases.

With none of the three configured (a local run) every caller is the owner, as before.

The one rule that matters for cost: a guest is never moved onto a paid model. If the free provider's limit is
reached, the guest is told to come back tomorrow; nothing falls back to OpenAI.

Guest codes are stored hashed: PRESCREEN_GUEST_CODES="label:sha256-of-code:YYYY-MM-DD,...", where the date is
the last day the code works. `python scripts/guest_code.py <label> --days 30` makes a code and prints the entry,
so the code itself is never written into the environment.

Allowances are counted in memory per UTC day, so a restart resets them. That is acceptable because guests only
ever use free models: the allowances share the free quota fairly, they don't protect money.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

FREE_PROVIDERS = {"groq"}

_DEFAULTS = {
    "GUEST_PRESCREENS_PER_DAY": 5,
    "GUEST_AGENT_REVIEWS_PER_DAY": 2,
    "GUEST_TOTAL_PRESCREENS_PER_DAY": 40,
    "GUEST_TOTAL_AGENT_REVIEWS_PER_DAY": 10,
}
_REPORT_ID = re.compile(r"^[0-9a-f]{16}$")


class AccessDenied(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


class AllowanceUsed(Exception):
    pass


@dataclass(frozen=True)
class Caller:
    role: str            # "owner" | "guest" | "public"
    label: str = ""      # guest label, e.g. "acme-jo"
    expires: str = ""    # guest: last day the code works

    @property
    def run_by(self) -> str:
        return "owner" if self.role == "owner" else f"{self.role}:{self.label}"

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _int(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, _DEFAULTS[name])))
    except ValueError:
        return _DEFAULTS[name]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def guest_codes() -> list[dict[str, str]]:
    out = []
    for item in os.environ.get("PRESCREEN_GUEST_CODES", "").split(","):
        parts = [p.strip() for p in item.strip().split(":")]
        if len(parts) == 3 and parts[0] and re.fullmatch(r"[0-9a-f]{64}", parts[1]):
            out.append({"label": parts[0], "hash": parts[1], "expires": parts[2]})
    return out


def public_examples() -> list[str]:
    return [x.strip() for x in os.environ.get("PRESCREEN_PUBLIC_EXAMPLES", "").split(",")
            if _REPORT_ID.fullmatch(x.strip())]


def request_url() -> str:
    """Where a visitor asks for a demo code. Shown on the page; a URL, never a code."""
    return os.environ.get("PRESCREEN_REQUEST_URL", "https://www.ai-toolstack.com/contact/")


def resolve(given: str, today: date | None = None) -> Caller:
    """The caller for this access code. Raises AccessDenied when the code is wrong or expired."""
    given = (given or "").strip()
    owner = os.environ.get("PRESCREEN_ACCESS_TOKEN", "")
    guests = guest_codes()
    examples = public_examples()
    if not owner and not guests and not examples:
        return Caller("owner")
    if owner and given and hmac.compare_digest(given, owner):
        return Caller("owner")
    if given:
        h = hash_code(given)
        for g in guests:
            if hmac.compare_digest(h, g["hash"]):
                today = today or date.today()
                try:
                    last = date.fromisoformat(g["expires"])
                except ValueError:
                    raise AccessDenied("This demo code isn't set up correctly. Ask for a new one.")
                if today > last:
                    raise AccessDenied(f"This demo code ended on {last:%d %b %Y}. Ask for a new one.")
                return Caller("guest", g["label"], g["expires"])
        raise AccessDenied("That code wasn't recognised.")
    if examples:
        return Caller("public")
    raise AccessDenied("Access code needed.")


# ---------------------------------------------------------------------------
# What each caller may see
# ---------------------------------------------------------------------------

def can_read(caller: Caller, payload: dict[str, Any]) -> bool:
    if caller.is_owner:
        return True
    if payload.get("report_id") in public_examples():
        return True
    return caller.role == "guest" and (payload.get("meta") or {}).get("run_by") == caller.run_by


def can_change(caller: Caller, payload: dict[str, Any]) -> bool:
    """Record a decision or run an agent review on this case. Examples are read-only for everyone but the owner."""
    if caller.is_owner:
        return True
    return caller.role == "guest" and (payload.get("meta") or {}).get("run_by") == caller.run_by


def visible_rows(caller: Caller, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if caller.is_owner:
        return rows
    examples = set(public_examples())
    return [r for r in rows if r.get("report_id") in examples
            or (caller.role == "guest" and r.get("run_by") == caller.run_by)]


def require_owner(caller: Caller) -> None:
    if not caller.is_owner:
        raise AccessDenied("This part of the service isn't available with a demo code.", 403)


def require_runner(caller: Caller) -> None:
    if caller.role == "public":
        raise AccessDenied("Enter a demo code to run your own pre-screen.")


# ---------------------------------------------------------------------------
# Daily allowances (guests only)
# ---------------------------------------------------------------------------

@dataclass
class _Day:
    day: str = ""
    per_code: dict[tuple[str, str], int] = field(default_factory=dict)
    total: dict[str, int] = field(default_factory=dict)


_DAY = _Day()
_KINDS = {"prescreen": ("GUEST_PRESCREENS_PER_DAY", "GUEST_TOTAL_PRESCREENS_PER_DAY", "pre-screens"),
          "agent_review": ("GUEST_AGENT_REVIEWS_PER_DAY", "GUEST_TOTAL_AGENT_REVIEWS_PER_DAY", "agent reviews")}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _roll() -> None:
    if _DAY.day != _today():
        _DAY.day, _DAY.per_code, _DAY.total = _today(), {}, {}


def spend(caller: Caller, kind: str) -> None:
    """Count one use for a guest, or raise AllowanceUsed. Owners aren't counted here (they have the hourly caps)."""
    if caller.role != "guest":
        return
    _roll()
    own_cap, total_cap, words = (_KINDS[kind][0], _KINDS[kind][1], _KINDS[kind][2])
    used = _DAY.per_code.get((caller.label, kind), 0)
    total = _DAY.total.get(kind, 0)
    if used >= _int(own_cap):
        raise AllowanceUsed(f"This demo code has used its {_int(own_cap)} {words} for today. "
                            "It resets at midnight UTC.")
    if total >= _int(total_cap):
        raise AllowanceUsed(f"The demo's {words} for today are used up. Please try again tomorrow.")
    _DAY.per_code[(caller.label, kind)] = used + 1
    _DAY.total[kind] = total + 1


def refund(caller: Caller, kind: str) -> None:
    """Give a use back when the run never started (company not found, a register down)."""
    if caller.role != "guest":
        return
    _roll()
    key = (caller.label, kind)
    if _DAY.per_code.get(key, 0) > 0:
        _DAY.per_code[key] -= 1
        _DAY.total[kind] = max(0, _DAY.total.get(kind, 0) - 1)


def allowance(caller: Caller) -> dict[str, Any] | None:
    if caller.role != "guest":
        return None
    _roll()
    out: dict[str, Any] = {"resets": "midnight UTC"}
    for kind, (own_cap, total_cap, _) in _KINDS.items():
        out[kind] = {"used": _DAY.per_code.get((caller.label, kind), 0), "limit": _int(own_cap),
                     "service_left": max(0, _int(total_cap) - _DAY.total.get(kind, 0))}
    return out


def reset_allowances() -> None:
    _DAY.day, _DAY.per_code, _DAY.total = "", {}, {}


# ---------------------------------------------------------------------------
# Models: guests on free providers only
# ---------------------------------------------------------------------------

class PaidModelRefused(Exception):
    pass


def guest_agent_model() -> dict[str, Any]:
    """Provider, model and judge setting for a guest's agent review. Refuses any provider not on the free list,
    so a mistyped setting can't put guests on a paid model."""
    provider = os.environ.get("GUEST_AGENT_PROVIDER", "groq").strip().lower()
    if provider not in FREE_PROVIDERS:
        raise PaidModelRefused(f"Guest agent reviews must use a free provider ({', '.join(sorted(FREE_PROVIDERS))});"
                               f" GUEST_AGENT_PROVIDER is {provider!r}.")
    return {"provider": provider,
            "model": os.environ.get("GUEST_AGENT_MODEL", "openai/gpt-oss-120b"),
            "judge": os.environ.get("GUEST_AGENT_JUDGE", "on").strip().lower() not in ("off", "0", "false", "no")}


def guest_news_model(provider: str, model: str) -> tuple[str, str]:
    """The pre-screen's one model call (reading news results). Kept as configured when it's already free;
    otherwise moved to Groq for guests."""
    if provider in FREE_PROVIDERS:
        return provider, model
    return "groq", os.environ.get("GUEST_CHAT_MODEL", "openai/gpt-oss-20b")

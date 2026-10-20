"""
Make a demo code for one person.

    python scripts/guest_code.py acme-jo --days 30

Prints the code (send it to the person) and the entry to add to PRESCREEN_GUEST_CODES on the Space
(comma-separated with any others). Only the hash goes into the environment, so the code itself is never stored.
To end a code early, delete its entry. Labels show in the case list as "guest:<label>", so use a name you'll
recognise, not the person's email.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prescreen.access import hash_code  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("label", help="short name for this person, letters, digits and - only")
    p.add_argument("--days", type=int, default=30, help="how many days the code works (default 30)")
    a = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]{2,40}", a.label):
        p.error("label: 2-40 letters, digits or -")
    code = "demo-" + secrets.token_urlsafe(12)
    last = date.today() + timedelta(days=max(1, a.days))
    print(f"Code for {a.label} (works until {last:%d %b %Y}):\n  {code}\n")
    print("Add to PRESCREEN_GUEST_CODES:")
    print(f"  {a.label}:{hash_code(code)}:{last.isoformat()}")


if __name__ == "__main__":
    main()

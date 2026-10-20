"""Store a SCRIPTED agent run for one demo report, so the Analysis tab and the "Drafted" email can be seen
offline (no model key). The agents' replies come from tests/test_agents_v2.polite; the MCP tools, guardrail
and trace are real. Usage: PRESCREEN_DATA_DIR=<dir> python tests/ui/seed_scripted_analysis.py <report id>"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("AGENT_MODEL", "gpt-6-luna")
os.environ.pop("AGENT_MCP_URL", None)
os.environ.setdefault("USE_MOCK_DATA", "true")

from src.agents_v2.service import run_agent_review  # noqa: E402
from tests.test_agents_v2 import polite  # noqa: E402

out = asyncio.run(run_agent_review(report_id=sys.argv[1], scripted=polite))
print(out["run_id"], [(s["agent"], s["status"]) for s in out["review"]["sections"]])

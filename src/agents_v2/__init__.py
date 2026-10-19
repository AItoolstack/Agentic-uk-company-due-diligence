"""
agents_v2: specialist agents on top of the pre-screen.

The pre-screen (src/prescreen) is the trustworthy core: written rules decide the route, and every check
that couldn't run says so. This package adds agents where judgement helps, and only through the MCP
server's nine tools:

  supervisor.py   LangGraph graph: loads the case over MCP, picks specialists, assembles the review.
  specialists.py  director history, news, broker email. Each is a small tool-using loop with its own
                  tool allowlist and a step limit.
  guardrail.py    checks every specialist output before anyone sees it. Blocks "clear" for a check that
                  couldn't run, any change to the route, sanctions detail, and facts not in the evidence.
  trace.py        every model call and tool call, with tokens, cost and time.
  model.py        the model client (OpenAI-compatible, JSON mode), with token usage captured.
  toolbox.py      the MCP client, in-process or over HTTP.
  service.py      entry point used by the API: run, store, rate limit.

What agents can't do, by construction: set or change the route, record a decision, or see sanctions detail
(the MCP server never returns it). The guardrail is the second line, for what they write.
"""

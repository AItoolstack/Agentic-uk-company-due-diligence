"""FastAPI tests that never invoke the real research graph."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from src.api.app import create_app

    return TestClient(create_app())


def _brief_dict() -> dict[str, Any]:
    return {
        "company_name": "Test Co Ltd",
        "overall_risk_level": "low",
        "overall_confidence": 0.8,
        "key_risks": [],
    }


class FakeBrief:
    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        assert mode == "json"
        return _brief_dict()


class FakeGraph:
    def __init__(
        self,
        *,
        final_state: dict[str, Any] | None = None,
        updates: list[dict[str, dict[str, Any]]] | None = None,
    ) -> None:
        self.final_state = final_state or {}
        self.updates = updates or []
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append(state)
        return self.final_state

    async def astream(
        self,
        state: dict[str, Any],
        stream_mode: str,
    ):
        assert stream_mode == "updates"
        self.invocations.append(state)
        for update in self.updates:
            yield update


@pytest.fixture
def install_fake_graph(monkeypatch):
    from src.api import routes

    def install(graph: FakeGraph) -> FakeGraph:
        monkeypatch.setattr(routes, "get_research_graph", lambda: graph)
        return graph

    return install


def _parse_sse(lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = ""
    for line in lines:
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append(
                (event_name, json.loads(line.removeprefix("data: ")))
            )
    return events


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "version" in response.json()


def test_cors_allows_configured_local_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:8000"
    )


def test_cors_does_not_allow_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/research/sync", {"query": "short"}),
        ("/research/sync", {}),
        ("/research/sync", {"query": "x" * 1001}),
        ("/research/stream", {"query": "hi"}),
    ],
)
def test_request_validation(
    client: TestClient,
    path: str,
    payload: dict[str, str],
) -> None:
    assert client.post(path, json=payload).status_code == 422


def test_sync_returns_brief_without_real_graph(
    client: TestClient,
    install_fake_graph,
) -> None:
    graph = install_fake_graph(
        FakeGraph(
            final_state={
                "due_diligence_brief": FakeBrief(),
                "errors": [],
            }
        )
    )

    response = client.post(
        "/research/sync",
        json={"query": "Create a due diligence brief for Monzo Bank"},
    )

    assert response.status_code == 200
    assert response.json()["brief"] == _brief_dict()
    assert response.json()["errors"] == []
    assert graph.invocations[0]["iteration_count"] == 1


def test_sync_serialises_agent_errors(
    client: TestClient,
    install_fake_graph,
) -> None:
    install_fake_graph(
        FakeGraph(
            final_state={
                "due_diligence_brief": None,
                "errors": [
                    {"agent": "test", "message": "typed failure"},
                    "plain failure",
                ],
            }
        )
    )

    response = client.post(
        "/research/sync",
        json={"query": "Due diligence brief for Revolut Ltd UK"},
    )

    assert response.status_code == 200
    assert response.json()["brief"] is None
    assert response.json()["errors"] == ["typed failure", "plain failure"]


def test_stream_content_type_and_events(
    client: TestClient,
    install_fake_graph,
) -> None:
    graph = install_fake_graph(
        FakeGraph(
            updates=[
                {
                    "query_understanding": {
                        "query_understanding": {
                            "entity_name": "Test Co Ltd",
                            "coverage_lines": [],
                        }
                    }
                },
                {
                    "synthesis": {
                        "due_diligence_brief": FakeBrief(),
                        "errors": [],
                    }
                },
            ]
        )
    )

    with client.stream(
        "POST",
        "/research/stream",
        json={"query": "Due diligence brief for Barclays Bank UK"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = _parse_sse(list(response.iter_lines()))

    assert [name for name, _ in events] == [
        "node_complete",
        "node_complete",
        "result",
        "done",
    ]
    assert events[0][1]["node"] == "query_understanding"
    assert events[1][1]["node"] == "synthesis"
    assert events[2][1]["brief"] == _brief_dict()
    assert graph.invocations[0]["iteration_count"] == 1


def test_stream_reports_fake_graph_failure(
    client: TestClient,
    install_fake_graph,
) -> None:
    class FailingGraph(FakeGraph):
        async def astream(self, state: dict[str, Any], stream_mode: str):
            raise RuntimeError("offline graph failure")
            yield

    install_fake_graph(FailingGraph())

    with client.stream(
        "POST",
        "/research/stream",
        json={"query": "Due diligence brief for Barclays Bank UK"},
    ) as response:
        events = _parse_sse(list(response.iter_lines()))

    assert events == [
        ("error", {"error": "offline graph failure"}),
    ]

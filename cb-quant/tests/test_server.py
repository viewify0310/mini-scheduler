import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn

from cbquant.runtime import WorkflowRun
from cbquant.server import LiveRun, create_app


@pytest.fixture
def base_url(tmp_path):
    app = create_app({"report_dir": str(tmp_path)})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def read_until(client, url, stop_status):
    """Follow the event stream until a 'done' update with the given status."""
    updates = []
    with client.stream("GET", url, timeout=20) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                updates.append(json.loads(line[len("data: "):]))
                if updates[-1]["type"] == "done" and updates[-1]["status"] == stop_status:
                    break
    return updates


def start_run(client):
    response = client.post("/api/runs", json={"step_delay": 0})
    assert response.status_code == 200
    return response.json()["run_id"]


def test_home_page_serves_the_viewer(base_url):
    response = httpx.get(base_url + "/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="graph"' in response.text


def test_graph_endpoint_describes_the_workflow(base_url):
    spec = httpx.get(base_url + "/api/graph").json()
    assert "risk_check" in [n["id"] for n in spec["nodes"]]
    assert {"from": "risk_check", "to": "human_approval", "route": "pass"} in spec["edges"]


def test_run_streams_updates_then_finishes_after_approval(base_url):
    with httpx.Client(base_url=base_url) as client:
        run_id = start_run(client)
        before = read_until(client, f"/api/runs/{run_id}/events", "waiting")
        assert {"type": "node", "node": "human_approval", "status": "waiting"} in before

        response = client.post(f"/api/runs/{run_id}/approval", json={"approved": True, "note": "ok"})
        assert response.status_code == 200

        everything = read_until(client, f"/api/runs/{run_id}/events", "completed")
        assert everything[: len(before)] == before  # a new viewer is replayed the history first
        assert {"type": "node", "node": "write_report", "status": "completed"} in everything


def test_approval_is_refused_when_the_run_is_not_waiting(base_url):
    with httpx.Client(base_url=base_url) as client:
        run_id = start_run(client)
        read_until(client, f"/api/runs/{run_id}/events", "waiting")
        assert client.post(f"/api/runs/{run_id}/approval", json={"approved": False}).status_code == 200
        read_until(client, f"/api/runs/{run_id}/events", "completed")
        assert client.post(f"/api/runs/{run_id}/approval", json={"approved": True}).status_code == 409


def test_unknown_run_is_not_found(base_url):
    assert httpx.get(base_url + "/api/runs/nope/events").status_code == 404
    assert httpx.post(base_url + "/api/runs/nope/approval", json={"approved": True}).status_code == 404


def test_live_run_holds_each_started_node_on_screen_for_the_step_delay(tmp_path):
    pauses = []

    async def fake_sleep(seconds):
        pauses.append(seconds)

    async def scenario():
        live = LiveRun(WorkflowRun({"report_dir": str(tmp_path)}), step_delay=0.4, sleep=fake_sleep)
        live.start()
        async for update in live.follow():
            if update["type"] == "done":
                break
        return live.history

    history = asyncio.run(scenario())
    started = [u for u in history if u["type"] == "node" and u["status"] == "running"]
    assert len(started) == 7
    assert pauses == [0.4] * 7

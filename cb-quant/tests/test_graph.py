import asyncio
from pathlib import Path

from cbquant.graph import build_workflow, graph_spec
from cbquant.runtime import WorkflowRun

FAIR_CSV = """bond_id,issuer,spot,vol,risk_free,credit_spread,dividend_yield,coupon_rate,coupon_freq,maturity_years,conversion_ratio,call_price,call_start_years,put_price,put_years,market_price
FAIR-1,ISS-1,82,0.32,0.03,0.020,0.00,0.010,2,3.0,1.00,,,,,105.0
"""


def statuses(updates, node):
    return [u["status"] for u in updates if u["type"] == "node" and u["node"] == node]


def outputs(updates, node):
    return [u for u in updates if u["type"] == "output" and u["node"] == node]


async def collect(stream):
    return [update async for update in stream]


def test_graph_spec_describes_nodes_and_routed_edges():
    spec = graph_spec(build_workflow())
    assert [n["id"] for n in spec["nodes"]] == [
        "__START__", "load_market_data", "price_convertibles", "generate_signals", "risk_check",
        "human_approval", "resize_positions", "record_decision", "write_report",
    ]
    edges = {(e["from"], e["to"]): e["route"] for e in spec["edges"]}
    assert edges[("risk_check", "human_approval")] == "pass"
    assert edges[("risk_check", "resize_positions")] == "breach"
    assert edges[("risk_check", "write_report")] == "no_trades"
    assert edges[("resize_positions", "risk_check")] is None
    assert edges[("record_decision", "write_report")] is None


def test_graph_spec_places_nodes_in_layers_from_the_start():
    layers = {n["id"]: n["layer"] for n in graph_spec(build_workflow())["nodes"]}
    assert layers["__START__"] == 0
    assert layers["risk_check"] == 4
    assert layers["human_approval"] == layers["resize_positions"] == 5
    assert layers["write_report"] == 7


def test_run_shrinks_the_book_to_fit_limits_then_waits_for_approval(tmp_path):
    async def scenario():
        run = WorkflowRun({"report_dir": str(tmp_path)})
        return await collect(run.start())

    updates = asyncio.run(scenario())

    assert [o["route"] for o in outputs(updates, "risk_check")] == ["breach", "pass"]
    assert statuses(updates, "risk_check") == ["running", "completed", "running", "completed"]
    assert statuses(updates, "resize_positions") == ["running", "completed"]
    assert statuses(updates, "human_approval") == ["running", "waiting"]
    assert statuses(updates, "write_report") == []

    (approval,) = [u for u in updates if u["type"] == "approval"]
    assert sorted(t["bond_id"] for t in approval["tickets"]) == [
        "SAMPLE-A-2029", "SAMPLE-A-2030", "SAMPLE-C-2029", "SAMPLE-F-2027",
    ]
    assert updates[-1] == {"type": "done", "status": "waiting"}


def test_approving_finishes_the_run_and_writes_the_report(tmp_path):
    async def scenario():
        run = WorkflowRun({"report_dir": str(tmp_path)})
        await collect(run.start())
        return await collect(run.resume(approved=True, note="확인"))

    updates = asyncio.run(scenario())

    assert statuses(updates, "human_approval") == ["completed"]
    assert statuses(updates, "write_report") == ["running", "completed"]
    assert updates[-1] == {"type": "done", "status": "completed"}
    (report,) = outputs(updates, "write_report")
    written = Path(report["output"]["path"])
    assert written.parent == tmp_path
    assert "승인" in written.read_text(encoding="utf-8")
    assert "SAMPLE-F-2027" in report["output"]["markdown"]


def test_rejecting_still_writes_a_report_marked_rejected(tmp_path):
    async def scenario():
        run = WorkflowRun({"report_dir": str(tmp_path)})
        await collect(run.start())
        return await collect(run.resume(approved=False, note=""))

    updates = asyncio.run(scenario())
    (report,) = outputs(updates, "write_report")
    assert "반려" in report["output"]["markdown"]


def test_resumed_run_does_not_repeat_updates_from_before_the_pause(tmp_path):
    async def scenario():
        run = WorkflowRun({"report_dir": str(tmp_path)})
        await collect(run.start())
        return await collect(run.resume(approved=True, note=""))

    updates = asyncio.run(scenario())
    assert outputs(updates, "load_market_data") == []
    assert statuses(updates, "risk_check") == []


def test_universe_with_nothing_cheap_skips_approval_and_reports(tmp_path):
    universe = tmp_path / "fair.csv"
    universe.write_text(FAIR_CSV)

    async def scenario():
        run = WorkflowRun({"universe_path": str(universe), "report_dir": str(tmp_path)})
        return await collect(run.start())

    updates = asyncio.run(scenario())
    assert [o["route"] for o in outputs(updates, "risk_check")] == ["no_trades"]
    assert statuses(updates, "human_approval") == []
    assert [u for u in updates if u["type"] == "approval"] == []
    assert updates[-1] == {"type": "done", "status": "completed"}
    assert "매매 대상 없음" in outputs(updates, "write_report")[0]["output"]["markdown"]


def test_failing_node_is_reported_as_failed_with_the_reason(tmp_path):
    async def scenario():
        run = WorkflowRun({"universe_path": str(tmp_path / "missing.csv"), "report_dir": str(tmp_path)})
        return await collect(run.start())

    updates = asyncio.run(scenario())
    assert statuses(updates, "load_market_data") == ["running", "failed"]
    assert updates[-1]["type"] == "done"
    assert updates[-1]["status"] == "failed"
    assert "missing.csv" in updates[-1]["error"]

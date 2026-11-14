"""The convertible bond workflow as an ADK graph.

    load_market_data -> price_convertibles -> generate_signals -> risk_check
    risk_check -[pass]------> human_approval -> record_decision -> write_report
    risk_check -[breach]----> resize_positions -> risk_check
    risk_check -[no_trades]-> write_report

Every node is ordinary code. Nodes read their inputs from the session state
by parameter name and write their results back to it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, Workflow

from .report import render_report
from .risk import RiskLimits, check, resize
from .signals import build_tickets, cheapness, classify
from .universe import SAMPLE_PATH, load_universe, value_row

DEFAULT_CONFIG = {
    "universe_path": str(SAMPLE_PATH),
    "report_dir": str(Path(__file__).resolve().parent.parent / "reports"),
    "threshold": 0.03,  # buy when the model is this far above the market
    "target_value": 200_000,  # market value per new position
    "limits": {"max_value_per_issuer": 300_000, "max_total_value": 700_000, "max_credit_spread": 0.06},
}

NODE_LABELS = {
    "__START__": ("시작", "start"),
    "load_market_data": ("시장 데이터 수집", "code"),
    "price_convertibles": ("CB 가격평가", "code"),
    "generate_signals": ("신호 생성", "code"),
    "risk_check": ("리스크 점검", "code"),
    "resize_positions": ("포지션 축소", "code"),
    "human_approval": ("사람 승인", "human"),
    "record_decision": ("결정 기록", "code"),
    "write_report": ("리포트 작성", "code"),
}


def load_market_data(ctx, universe_path: str):
    rows = load_universe(universe_path)
    ctx.state["universe"] = rows
    return {"bonds": len(rows), "source": Path(universe_path).name}


async def price_convertibles(ctx, universe: list):
    valuations = [await asyncio.to_thread(value_row, row) for row in universe]
    ctx.state["valuations"] = valuations
    return {"valuations": valuations}


def generate_signals(ctx, valuations: list, threshold: float, target_value: float):
    tickets = build_tickets(valuations, target_value=target_value, threshold=threshold)
    ctx.state["tickets"] = tickets
    signals = []
    for v in valuations:
        cheap = cheapness(v["market_price"], v["model_price"])
        signals.append({"bond_id": v["bond_id"], "cheapness": cheap, "action": classify(cheap, threshold)})
    return {"signals": signals, "tickets": tickets}


def risk_check(tickets: list, limits: dict):
    if not tickets:
        return Event(output={"breaches": [], "tickets": 0}, route="no_trades")
    breaches = check(tickets, RiskLimits(**limits))
    total = sum(t["market_value"] for t in tickets)
    return Event(
        output={"breaches": breaches, "tickets": len(tickets), "total_value": total},
        route="breach" if breaches else "pass",
    )


def resize_positions(ctx, tickets: list, limits: dict):
    resized = resize(tickets, RiskLimits(**limits))
    ctx.state["tickets"] = resized
    kept = {t["bond_id"] for t in resized}
    return {"dropped": [t["bond_id"] for t in tickets if t["bond_id"] not in kept], "tickets": resized}


async def human_approval(tickets: list):
    # The run pauses here; the reply to this request becomes this node's output.
    yield RequestInput(message="리스크 점검을 통과한 티켓입니다. 승인하시겠습니까?", payload={"tickets": tickets})


def record_decision(ctx, node_input: dict):
    decision = {"approved": bool(node_input.get("approved")), "note": node_input.get("note", "")}
    ctx.state["decision"] = decision
    return decision


def write_report(ctx, valuations: list, tickets: list, report_dir: str):
    markdown = render_report(valuations, tickets, ctx.state.get("decision"))
    folder = Path(report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"cb-report-{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(markdown, encoding="utf-8")
    return {"path": str(path), "markdown": markdown}


def build_workflow() -> Workflow:
    return Workflow(
        name="cb_quant",
        edges=[
            (START, load_market_data, price_convertibles, generate_signals, risk_check),
            (risk_check, {"pass": human_approval, "breach": resize_positions, "no_trades": write_report}),
            (resize_positions, risk_check),
            (human_approval, record_decision, write_report),
        ],
    )


def graph_spec(workflow: Workflow) -> dict:
    """Describe the graph for the viewer: nodes with a layer and column, and edges."""
    names = [n.name for n in workflow.graph.nodes]
    edges = [{"from": e.from_node.name, "to": e.to_node.name, "route": e.route} for e in workflow.graph.edges]

    # Layer = longest path from the start, ignoring edges that loop back.
    layer: dict[str, int] = {}
    visiting: set[str] = set()

    def place(name: str, depth: int) -> None:
        if name in visiting or layer.get(name, -1) >= depth:
            return
        layer[name] = depth
        visiting.add(name)
        for edge in edges:
            if edge["from"] == name:
                place(edge["to"], depth + 1)
        visiting.discard(name)

    place(names[0], 0)

    columns: dict[int, int] = {}
    nodes = []
    for name in sorted(names, key=lambda n: (layer[n], names.index(n))):
        label, kind = NODE_LABELS.get(name, (name, "code"))
        column = columns.get(layer[name], 0)
        columns[layer[name]] = column + 1
        nodes.append({"id": name, "label": label, "kind": kind, "layer": layer[name], "column": column})
    return {"name": workflow.name, "nodes": nodes, "edges": edges}

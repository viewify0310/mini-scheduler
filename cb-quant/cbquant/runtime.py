"""Run the workflow and turn ADK events into updates for the live viewer.

Updates are plain dicts:
    {"type": "node", "node": name, "status": "running" | "completed" | "waiting" | "failed"}
    {"type": "output", "node": name, "output": ..., "route": ...}
    {"type": "approval", "message": ..., "tickets": [...]}
    {"type": "done", "status": "completed" | "waiting" | "failed", "error": ...}
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import InMemoryRunner
from google.adk.workflow._node_status import NodeStatus
from google.adk.workflow.utils._workflow_hitl_utils import create_request_input_response
from google.genai import types

from .graph import DEFAULT_CONFIG, build_workflow

_USER = "trader"
_SHOWN = {
    NodeStatus.RUNNING.value: "running",
    NodeStatus.COMPLETED.value: "completed",
    NodeStatus.WAITING.value: "waiting",
    NodeStatus.FAILED.value: "failed",
}


def _node_name(path: str) -> str:
    """'cb_quant@1/risk_check@2' -> 'risk_check'."""
    return path.rsplit("/", 1)[-1].split("@", 1)[0]


class WorkflowRun:
    """One run of the workflow, from start through an optional approval pause."""

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        workflow = build_workflow()
        app = App(name=workflow.name, root_agent=workflow, resumability_config=ResumabilityConfig(is_resumable=True))
        self._app_name = workflow.name
        self._runner = InMemoryRunner(app=app)
        self._session_id: str | None = None
        self._invocation_id: str | None = None
        self._interrupt_id: str | None = None
        self._status: dict[str, str] = {}
        self._seen_outputs: set[str] = set()

    async def start(self) -> AsyncIterator[dict]:
        session = await self._runner.session_service.create_session(
            app_name=self._app_name, user_id=_USER, state=self.config
        )
        self._session_id = session.id
        message = types.Content(role="user", parts=[types.Part(text="run")])
        async for update in self._drive(new_message=message):
            yield update

    async def resume(self, approved: bool, note: str = "") -> AsyncIterator[dict]:
        if self._interrupt_id is None:
            raise RuntimeError("this run is not waiting for approval")
        reply = create_request_input_response(self._interrupt_id, {"approved": approved, "note": note})
        self._interrupt_id = None
        message = types.Content(role="user", parts=[reply])
        async for update in self._drive(new_message=message, invocation_id=self._invocation_id):
            yield update

    async def _drive(self, **run_args) -> AsyncIterator[dict]:
        try:
            async for event in self._runner.run_async(user_id=_USER, session_id=self._session_id, **run_args):
                self._invocation_id = event.invocation_id
                for update in self._translate(event):
                    yield update
        except Exception as error:  # a node raised; show which one and why
            for name, status in self._status.items():
                if status == "running":
                    self._status[name] = "failed"
                    yield {"type": "node", "node": name, "status": "failed"}
            yield {"type": "done", "status": "failed", "error": f"{type(error).__name__}: {error}"}
            return
        yield {"type": "done", "status": "waiting" if self._interrupt_id else "completed"}

    def _translate(self, event) -> list[dict]:
        updates: list[dict] = []

        node_states = (event.actions.agent_state or {}).get("nodes", {})
        for name, state in node_states.items():
            status = _SHOWN.get(state.get("status"))
            if status and self._status.get(name) != status:
                self._status[name] = status
                updates.append({"type": "node", "node": name, "status": status})

        path = event.node_info.path
        if path and event.long_running_tool_ids:
            call = next(part.function_call for part in event.content.parts if part.function_call)
            self._interrupt_id = call.id
            updates.append(
                {"type": "approval", "message": call.args.get("message"), "tickets": call.args["payload"]["tickets"]}
            )
        elif path and event.output is not None and path not in self._seen_outputs:
            self._seen_outputs.add(path)
            updates.append(
                {"type": "output", "node": _node_name(path), "output": event.output, "route": event.actions.route}
            )
        return updates

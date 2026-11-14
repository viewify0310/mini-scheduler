"""Local web server: runs the workflow and streams its progress to the viewer.

    python -m cbquant.server          # then open http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import json
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

from .graph import build_workflow, graph_spec
from .runtime import WorkflowRun

STATIC = Path(__file__).resolve().parent / "static"


class LiveRun:
    """A workflow run plus the history of its updates, for any number of viewers."""

    def __init__(
        self,
        run: WorkflowRun,
        step_delay: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.run = run
        self.step_delay = step_delay
        self.history: list[dict] = []
        self.state = "running"  # running | waiting | completed | failed
        self._sleep = sleep
        self._changed = asyncio.Condition()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._pump(self.run.start()))

    def approve(self, approved: bool, note: str = "") -> None:
        if self.state != "waiting":
            raise RuntimeError("this run is not waiting for approval")
        self.state = "running"
        self._task = asyncio.create_task(self._pump(self.run.resume(approved, note)))

    async def _pump(self, stream: AsyncIterator[dict]) -> None:
        async for update in stream:
            if update["type"] == "done":
                self.state = update["status"]
            self.history.append(update)
            async with self._changed:
                self._changed.notify_all()
            # Steps finish in milliseconds; hold each one so it can be seen.
            if self.step_delay and update["type"] == "node" and update["status"] == "running":
                await self._sleep(self.step_delay)

    async def follow(self) -> AsyncIterator[dict]:
        """Yield every update from the beginning, then new ones until the run ends."""
        index = 0
        while True:
            while index < len(self.history):
                yield self.history[index]
                index += 1
            if self.state in ("completed", "failed"):
                return
            async with self._changed:
                await self._changed.wait_for(lambda: index < len(self.history))


class RunRequest(BaseModel):
    step_delay: float = Field(default=0.6, ge=0, le=5)


class ApprovalRequest(BaseModel):
    approved: bool
    note: str = ""


def create_app(config: dict | None = None) -> FastAPI:
    app = FastAPI(title="CB Quant Workflow")
    runs: dict[str, LiveRun] = {}

    def find(run_id: str) -> LiveRun:
        if run_id not in runs:
            raise HTTPException(status_code=404, detail="unknown run")
        return runs[run_id]

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/api/graph")
    def graph() -> dict:
        return graph_spec(build_workflow())

    @app.post("/api/runs")
    async def start_run(request: RunRequest) -> dict:
        run_id = uuid.uuid4().hex[:12]
        runs[run_id] = LiveRun(WorkflowRun(config), step_delay=request.step_delay)
        runs[run_id].start()
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        live = find(run_id)

        async def stream() -> AsyncIterator[str]:
            async for update in live.follow():
                yield f"data: {json.dumps(update, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.post("/api/runs/{run_id}/approval")
    async def approval(run_id: str, request: ApprovalRequest) -> dict:
        live = find(run_id)
        try:
            live.approve(request.approved, request.note)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="CB quant workflow with a live graph viewer")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Open http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
mini_scheduler.py — a single-file desktop task scheduler.

Dark-mode GUI (customtkinter) with a status table, full task CRUD, a
background scheduler thread, per-task logs, and a built-in log viewer.

All state lives in  ~/.mini_scheduler/tasks.json  so tasks survive
restarts. Each task's output is appended to  ~/.mini_scheduler/logs/<id>.log

Dependency:  pip install customtkinter   (everything else is stdlib)
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk


# ───────────────────────── paths & constants ─────────────────────────
APP_DIR = Path.home() / ".mini_scheduler"
TASKS_FILE = APP_DIR / "tasks.json"
LOGS_DIR = APP_DIR / "logs"

INITIAL_DELAY = 5      # seconds before a new/enabled task first fires
TICK_SECONDS = 1       # how often the scheduler thread polls
UI_REFRESH_MS = 1000   # how often the table redraws

# task status values (stored in tasks.json)
ST_PENDING = "pending"
ST_IDLE = "idle"
ST_RUNNING = "running"
ST_FAILED = "failed"
ST_DISABLED = "disabled"

STATUS_LABEL = {
    ST_PENDING:  "🔵  Pending",
    ST_IDLE:     "🟢  Idle",
    ST_RUNNING:  "🟡  Running",
    ST_FAILED:   "🔴  Failed",
    ST_DISABLED: "⚪  Disabled",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ───────────────────────── helpers ─────────────────────────
def now_ts() -> float:
    return time.time()


def fmt_ts(ts: float | None) -> str:
    """Epoch timestamp -> readable string, or an em dash when empty."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def log_path(task_id: str) -> Path:
    return LOGS_DIR / f"{task_id}.log"


def compute_next(task: dict, base: float | None = None) -> float:
    """Next fire time for a task, as an epoch timestamp.

    Only 'interval' schedules are implemented. To add cron support, branch
    on task['schedule']['type'] here (e.g. use the croniter library).
    """
    base = now_ts() if base is None else base
    sched = task.get("schedule", {})
    if sched.get("type") == "interval":
        minutes = max(0.05, float(sched.get("minutes", 10)))
        return base + minutes * 60
    return base + 365 * 24 * 3600  # unknown schedule: effectively never


# ───────────────────────── task store ─────────────────────────
class TaskStore:
    """Holds the task list and persists it to tasks.json.

    A task dict looks like:
        {
          "id": "<uuid hex>",
          "name": "Nightly Backup",
          "command": "C:\\\\scripts\\\\backup.bat",
          "schedule": {"type": "interval", "minutes": 10},
          "enabled": True,
          "status": "idle",
          "last_run": 1718000000.0 | None,
          "last_exit": 0 | None,
          "next_run": 1718000600.0 | None,
        }

    All methods are guarded by a re-entrant lock, so the UI thread and the
    scheduler thread can share one store safely.
    """

    def __init__(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tasks: list[dict] = []
        self.load()

    def load(self) -> None:
        with self.lock:
            if not TASKS_FILE.exists():
                self.tasks = []
                return
            try:
                data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
                self.tasks = data if isinstance(data, list) else data.get("tasks", [])
            except Exception:
                self.tasks = []
            # A task can't still be "running" after a restart — the process
            # is long gone. Reconcile status with the last known exit code.
            for t in self.tasks:
                t.setdefault("status", ST_PENDING)
                if not t.get("enabled", True):
                    t["status"] = ST_DISABLED
                elif t["status"] == ST_RUNNING:
                    t["status"] = ST_IDLE if t.get("last_exit") in (0, None) else ST_FAILED

    def save(self) -> None:
        with self.lock:
            tmp = TASKS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.tasks, indent=2), encoding="utf-8")
            tmp.replace(TASKS_FILE)

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            return next((t for t in self.tasks if t["id"] == task_id), None)

    def snapshot(self) -> list[dict]:
        """A deep-ish copy of the task list, safe to read off-lock."""
        with self.lock:
            return [dict(t) for t in self.tasks]

    def add(self, task: dict) -> None:
        with self.lock:
            self.tasks.append(task)
            self.save()

    def delete(self, task_id: str) -> None:
        with self.lock:
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            self.save()


# ───────────────────────── scheduler ─────────────────────────
class Scheduler(threading.Thread):
    """Background thread. Every TICK_SECONDS it launches any due tasks and
    reaps finished ones. It is UI-independent — the App just polls state.
    """

    def __init__(self, store: TaskStore) -> None:
        super().__init__(daemon=True)
        self.store = store
        self._stop = threading.Event()
        # task_id -> {"proc": Popen, "log": file handle, "started": ts}
        self.running: dict[str, dict] = {}

    # -- thread lifecycle --------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # never let the loop die
                print(f"[scheduler] tick error: {exc}", file=sys.stderr)
            self._stop.wait(TICK_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        # Terminate anything still running so we don't orphan processes.
        for info in list(self.running.values()):
            try:
                info["proc"].terminate()
            except Exception:
                pass
            try:
                info["log"].close()
            except Exception:
                pass

    # -- one scheduler step ------------------------------------------
    def tick(self) -> None:
        now = now_ts()
        with self.store.lock:
            self._reap(now)
            for task in self.store.tasks:
                if not task.get("enabled", True) or task["id"] in self.running:
                    continue
                nxt = task.get("next_run")
                if nxt is None:
                    task["next_run"] = now + INITIAL_DELAY
                elif now >= nxt:
                    self._launch(task, now, trigger="scheduled")

    def launch_now(self, task_id: str) -> None:
        """Run a task immediately, regardless of its schedule."""
        with self.store.lock:
            task = self.store.get(task_id)
            if task and task_id not in self.running:
                self._launch(task, now_ts(), trigger="manual")

    # -- launching / reaping (always called with store.lock held) -----
    def _launch(self, task: dict, now: float, trigger: str) -> None:
        cmd = (task.get("command") or "").strip()
        path = log_path(task["id"])

        if not cmd:
            with path.open("ab") as fh:
                fh.write(f"\n{'=' * 60}\n[{fmt_ts(now)}] (no command set)\n"
                         .encode("utf-8"))
            self._finish(task, now, exit_code=None)
            return

        # If the command is exactly a file path, quote it (handles spaces)
        # and run from its own folder so relative paths inside it work.
        candidate = cmd.strip('"')
        cand_path = Path(candidate)
        if cand_path.is_file():
            run_cmd, workdir = f'"{candidate}"', str(cand_path.parent)
        else:
            run_cmd, workdir = cmd, None

        logfh = path.open("ab")
        logfh.write((f"\n{'=' * 60}\n[{fmt_ts(now)}] {trigger} run — {cmd}\n"
                     f"{'=' * 60}\n").encode("utf-8"))
        logfh.flush()

        try:
            proc = subprocess.Popen(
                run_cmd,
                shell=True,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=logfh,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            logfh.write(f"[launch failed] {exc}\n".encode("utf-8"))
            logfh.close()
            self._finish(task, now, exit_code=None)
            return

        self.running[task["id"]] = {"proc": proc, "log": logfh, "started": now}
        task["status"] = ST_RUNNING
        task["last_run"] = now
        self.store.save()

    def _reap(self, now: float) -> None:
        finished = []
        for task_id, info in list(self.running.items()):
            code = info["proc"].poll()
            if code is not None:
                finished.append((task_id, info, code))

        for task_id, info, code in finished:
            del self.running[task_id]
            dur = now - info["started"]
            try:
                info["log"].write(
                    f"\n[{fmt_ts(now)}] finished — exit {code} ({dur:.1f}s)\n"
                    .encode("utf-8"))
                info["log"].close()
            except Exception:
                pass
            task = self.store.get(task_id)
            if task:
                self._finish(task, now, exit_code=code, save=False)
        if finished:
            self.store.save()

    def _finish(self, task: dict, now: float, exit_code: int | None,
                save: bool = True) -> None:
        """Record the outcome of a run and schedule the next one."""
        task["last_run"] = now
        task["last_exit"] = exit_code
        task["next_run"] = compute_next(task, now)
        if not task.get("enabled", True):
            task["status"] = ST_DISABLED
        elif exit_code == 0:
            task["status"] = ST_IDLE
        else:
            task["status"] = ST_FAILED
        if save:
            self.store.save()


# ───────────────────────── add / edit dialog ─────────────────────────
class TaskDialog(ctk.CTkToplevel):
    """Add-or-edit form. Calls on_save(data_dict) when the user saves."""

    def __init__(self, parent, on_save, task: dict | None = None) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.title("Edit Task" if task else "New Task")
        self.geometry("470x430")
        self.resizable(False, False)
        self.transient(parent)

        pad = {"padx": 18, "pady": (10, 0)}

        ctk.CTkLabel(self, text="Name").pack(anchor="w", **pad)
        self.name_var = tk.StringVar(value=task["name"] if task else "")
        self.name_entry = ctk.CTkEntry(self, textvariable=self.name_var, width=430)
        self.name_entry.pack(padx=18, pady=(4, 0))

        ctk.CTkLabel(self, text="Command").pack(anchor="w", **pad)
        cmd_row = ctk.CTkFrame(self, fg_color="transparent")
        cmd_row.pack(fill="x", padx=18, pady=(4, 0))
        self.cmd_var = tk.StringVar(value=task["command"] if task else "")
        ctk.CTkEntry(cmd_row, textvariable=self.cmd_var).pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(cmd_row, text="Browse", width=80,
                      command=self._browse).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(self, text="Schedule").pack(anchor="w", **pad)
        sched_row = ctk.CTkFrame(self, fg_color="transparent")
        sched_row.pack(fill="x", padx=18, pady=(4, 0))
        self.type_var = tk.StringVar(value="Interval")
        ctk.CTkOptionMenu(sched_row, values=["Interval"],
                          variable=self.type_var, width=120).pack(side="left")
        ctk.CTkLabel(sched_row, text="every").pack(side="left", padx=8)
        sched = (task or {}).get("schedule", {})
        self.every_var = tk.StringVar(value=str(sched.get("minutes", 10)))
        ctk.CTkEntry(sched_row, textvariable=self.every_var, width=60).pack(side="left")
        ctk.CTkLabel(sched_row, text="minutes").pack(side="left", padx=8)

        self.enabled_var = tk.BooleanVar(
            value=task.get("enabled", True) if task else True)
        ctk.CTkCheckBox(self, text="Enabled",
                        variable=self.enabled_var).pack(anchor="w", **pad)

        self.err = ctk.CTkLabel(self, text="", text_color="#ff6b6b")
        self.err.pack(anchor="w", padx=18, pady=(10, 0))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(btn_row, text="Cancel", width=90, fg_color="#444",
                      hover_color="#555", command=self.destroy).pack(side="right")
        ctk.CTkButton(btn_row, text="Save", width=90,
                      command=self._save).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(200, self._on_shown)

    def _on_shown(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()          # make the dialog modal
            self.lift()
            self.name_entry.focus_set()
        except Exception:
            pass

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select a script or program",
            filetypes=[("Scripts & programs", "*.bat *.cmd *.exe *.ps1 *.py"),
                       ("All files", "*.*")],
        )
        if path:
            self.cmd_var.set(path)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        cmd = self.cmd_var.get().strip()
        if not name:
            self.err.configure(text="Name is required.")
            return
        if not cmd:
            self.err.configure(text="Command is required.")
            return
        try:
            minutes = float(self.every_var.get())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            self.err.configure(text="Interval must be a positive number.")
            return
        self.on_save({
            "name": name,
            "command": cmd,
            "schedule": {"type": "interval", "minutes": minutes},
            "enabled": bool(self.enabled_var.get()),
        })
        self.destroy()


# ───────────────────────── log viewer ─────────────────────────
class LogViewer(ctk.CTkToplevel):
    """Dark log viewer with an optional 2-second auto-refresh switch."""

    def __init__(self, parent, task_name: str, path: Path) -> None:
        super().__init__(parent)
        self.path = path
        self.auto = False
        self.title(f"Log — {task_name}")
        self.geometry("780x500")
        self.transient(parent)

        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x")
        ctk.CTkButton(bar, text="Reload", width=80,
                      command=self._load).pack(side="left", padx=6, pady=8)
        ctk.CTkButton(bar, text="Clear", width=80, fg_color="#a23",
                      hover_color="#c33", command=self._clear).pack(
                      side="left", padx=6, pady=8)
        ctk.CTkSwitch(bar, text="Auto-refresh",
                      command=self._toggle_auto).pack(side="right", padx=12)

        self.text = ctk.CTkTextbox(self, wrap="word", font=("Consolas", 12))
        self.text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.bind("<Escape>", lambda e: self.destroy())
        self._load()

    def _load(self):
        self.text.delete("1.0", "end")
        if self.path.exists():
            try:    self.text.insert("1.0", self.path.read_text(encoding="utf-8", errors="replace"))
            except Exception as e: self.text.insert("1.0", f"(error reading log: {e})")
        else:
            self.text.insert("1.0", "(no log yet)")
        self.text.see("end")

    def _clear(self):
        if messagebox.askyesno("Clear log", "Erase log file?", parent=self):
            self.path.write_text(""); self._load()

    def _toggle_auto(self):
        self.auto = not self.auto
        if self.auto: self._auto()

    def _auto(self):
        if not self.auto or not self.winfo_exists(): return
        self._load()
        self.after(2000, self._auto)


# ───────────────────────── main window ─────────────────────────
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mini Scheduler")
        self.geometry("980x540")
        self.minsize(780, 400)

        self.store = TaskStore()
        self.scheduler = Scheduler(self.store)

        self._build_toolbar()
        self._build_table()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.scheduler.start()
        self._refresh_table()

    # -- UI construction ---------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x")

        def btn(text, cmd, **kw):
            ctk.CTkButton(bar, text=text, width=96, command=cmd, **kw).pack(
                side="left", padx=6, pady=10)

        btn("+  New", self._new_task)
        btn("Edit", self._edit_task)
        btn("Delete", self._delete_task, fg_color="#a23", hover_color="#c33")
        btn("Run Now", self._run_now)
        btn("Toggle", self._toggle_task)
        btn("View Log", self._view_log)

        self.status_lbl = ctk.CTkLabel(bar, text="", text_color="#888")
        self.status_lbl.pack(side="right", padx=14)

    def _build_table(self) -> None:
        wrap = ctk.CTkFrame(self, corner_radius=8)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # ttk's native Windows theme ignores colours — switch to "clam"
        # so the dark Treeview styling actually takes effect.
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#242424", fieldbackground="#242424",
                        foreground="#dcdcdc", rowheight=30, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#1a1a1a",
                        foreground="#bbbbbb", relief="flat", padding=6,
                        font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f6aa5")],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#2a2a2a")])

        cols = ("status", "name", "command", "schedule", "last", "next")
        headers = {"status": "Status", "name": "Name", "command": "Command",
                   "schedule": "Schedule", "last": "Last Run", "next": "Next Run"}
        widths = {"status": 110, "name": 170, "command": 280,
                  "schedule": 120, "last": 150, "next": 150}
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c],
                             anchor="w" if c in ("name", "command") else "center")
        self.tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)

        sb = ctk.CTkScrollbar(wrap, command=self.tree.yview)
        sb.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.tag_configure("failed", foreground="#ff6b6b")
        self.tree.tag_configure("running", foreground="#ffd166")
        self.tree.tag_configure("disabled", foreground="#777777")

        self.tree.bind("<Double-1>", lambda e: self._edit_task())

    # -- live refresh -------------------------------------------------
    def _refresh_table(self) -> None:
        if not self.winfo_exists():
            return
        selected = self._selected_id()
        tasks = self.store.snapshot()

        self.tree.delete(*self.tree.get_children())
        running = 0
        for t in tasks:
            status = t.get("status", ST_PENDING)
            running += status == ST_RUNNING
            sched = t.get("schedule", {})
            sched_txt = (f"every {sched.get('minutes', '?')} min"
                         if sched.get("type") == "interval"
                         else sched.get("type", "—"))
            nxt = t.get("next_run") if t.get("enabled", True) else None
            tag = status if status in ("failed", "running", "disabled") else ""
            self.tree.insert(
                "", "end", iid=t["id"], tags=((tag,) if tag else ()),
                values=(STATUS_LABEL.get(status, status),
                        t.get("name", ""), t.get("command", ""),
                        sched_txt, fmt_ts(t.get("last_run")), fmt_ts(nxt)))

        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        self.status_lbl.configure(
            text=f"{len(tasks)} task(s)  ·  {running} running")
        self.after(UI_REFRESH_MS, self._refresh_table)

    # -- selection helpers -------------------------------------------
    def _selected_id(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _selected_task(self) -> dict | None:
        tid = self._selected_id()
        return self.store.get(tid) if tid else None

    def _need_selection(self) -> dict | None:
        task = self._selected_task()
        if not task:
            messagebox.showinfo("No task", "Select a task first.", parent=self)
        return task

    # -- toolbar actions ---------------------------------------------
    def _new_task(self) -> None:
        TaskDialog(self, on_save=self._save_new)

    def _save_new(self, data: dict) -> None:
        enabled = data["enabled"]
        self.store.add({
            "id": uuid.uuid4().hex,
            "name": data["name"],
            "command": data["command"],
            "schedule": data["schedule"],
            "enabled": enabled,
            "status": ST_PENDING if enabled else ST_DISABLED,
            "last_run": None,
            "last_exit": None,
            "next_run": now_ts() + INITIAL_DELAY if enabled else None,
        })
        self._refresh_table()

    def _edit_task(self) -> None:
        task = self._need_selection()
        if task:
            TaskDialog(self, task=task,
                       on_save=lambda d: self._save_edit(task["id"], d))

    def _save_edit(self, task_id: str, data: dict) -> None:
        with self.store.lock:
            t = self.store.get(task_id)
            if not t:
                return
            t["name"] = data["name"]
            t["command"] = data["command"]
            t["schedule"] = data["schedule"]
            t["enabled"] = data["enabled"]
            if data["enabled"]:
                if t["status"] == ST_DISABLED:
                    t["status"] = ST_PENDING
                t["next_run"] = now_ts() + INITIAL_DELAY
            else:
                t["status"] = ST_DISABLED
                t["next_run"] = None
            self.store.save()
        self._refresh_table()

    def _delete_task(self) -> None:
        task = self._need_selection()
        if task and messagebox.askyesno(
                "Delete task", f"Delete “{task['name']}”?", parent=self):
            self.store.delete(task["id"])
            self._refresh_table()

    def _run_now(self) -> None:
        task = self._need_selection()
        if task:
            self.scheduler.launch_now(task["id"])
            self._refresh_table()

    def _toggle_task(self) -> None:
        task = self._need_selection()
        if not task:
            return
        with self.store.lock:
            t = self.store.get(task["id"])
            enabled = not t.get("enabled", True)
            t["enabled"] = enabled
            if enabled:
                if t["status"] == ST_DISABLED:
                    t["status"] = ST_PENDING
                t["next_run"] = now_ts() + INITIAL_DELAY
            else:
                t["status"] = ST_DISABLED
                t["next_run"] = None
            self.store.save()
        self._refresh_table()

    def _view_log(self) -> None:
        task = self._need_selection()
        if task:
            LogViewer(self, task["name"], log_path(task["id"]))

    # -- shutdown -----------------------------------------------------
    def _on_close(self) -> None:
        self.scheduler.stop()
        self.destroy()


# ───────────────────────── run ─────────────────────────
if __name__ == "__main__":
    App().mainloop()

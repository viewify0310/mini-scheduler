# Mini Scheduler

A single-file desktop task scheduler with a dark UI. Define commands, run
them on an interval, and watch their status, last run time, and full output
from a built-in log viewer. All state is saved, so tasks survive restarts.

## 1. Install the one dependency

Install `customtkinter` into the Python environment you'll launch it from:

```cmd
python -m pip install customtkinter
```

(If you run it from a specific virtual env or Conda env, call that env's
`python.exe` instead of the bare `python`.) Everything else is Python
standard library.

## 2. Run it

```cmd
python mini_scheduler.py
```

Or use `run.bat` — open it, set `PYTHONW` to your env's `pythonw.exe`, then
double-click it. `pythonw.exe` hides the console window. `run.bat` finds
`mini_scheduler.py` next to itself, so the folder can live anywhere.

## Auto-start at login

Run `setup-autostart.ps1` to make Mini Scheduler launch every time you log
in (it creates a shortcut in your Windows Startup folder, pointing at
`pythonw.exe` so no console window shows):

```cmd
powershell -ExecutionPolicy Bypass -File setup-autostart.ps1
```

To stop it auto-starting, run the same script with `-Remove` (or delete
`Mini Scheduler.lnk` from the `shell:startup` folder).

## 3. Use it

**Add a task** — click **+ New**, fill in:

- **Name** — any label, e.g. `Nightly Backup`
- **Command** — click **Browse** to pick a `.bat`/`.exe`/`.py`, or type a
  full command line. If it's a single file path, it runs from that file's
  folder so relative paths inside the script work.
- **Schedule** — Interval, every N minutes
- **Enabled** — tick to schedule it

A new task fires ~5 seconds after you save it, then every interval.

| Status         | Meaning                              |
| -------------- | ------------------------------------ |
| 🔵 Pending     | Never run yet                        |
| 🟢 Idle        | Last run succeeded (exit 0)          |
| 🟡 Running     | Currently executing                  |
| 🔴 Failed      | Last run exited non-zero / failed    |
| ⚪ Disabled    | Toggled off — won't fire             |

Other actions: **double-click** a row to edit · **Run Now** fires
immediately regardless of schedule · **Toggle** enables/disables without
deleting · **View Log** opens the dark log viewer (with a 2-second
auto-refresh switch).

## Where state lives

| Path                            | Contents                          |
| -------------------------------- | --------------------------------- |
| `~/.mini_scheduler/tasks.json`   | All task definitions + status     |
| `~/.mini_scheduler/logs/<id>.log`| Per-task command output           |

(`~` is your user folder, e.g. `C:\Users\<you>`.) Back up `tasks.json` to
keep your tasks; delete the folder to reset everything.

## Architecture

```
App (UI / main thread)  ──reads/writes──►  TaskStore  ──►  tasks.json
        │                                      ▲
        │ polls every 1s                       │ shared, lock-guarded
        ▼                                      │
   ttk.Treeview                          Scheduler (background thread)
                                         - ticks every 1s
                                         - subprocess.Popen per due task
                                         - reaps + records exit codes
```

## Extending it

- **Cron schedules** — branch in `compute_next()` on the schedule type and
  use the `croniter` library.
- **Alerts on failure** — hook into `Scheduler._finish()` when
  `exit_code != 0` (send email/Slack).
- **Concurrency limit** — cap `len(self.running)` in `Scheduler.tick()`.
- **Run history / charts** — append each `_finish()` to a separate JSON.
- **System tray** — add `pystray` so it minimizes to the tray.

## Also in this repository

[`cb-quant/`](cb-quant/README.md) — a convertible bond quant workflow built
as a Google ADK graph, with a live graph viewer in the browser. It is a
separate tool with its own README and dependencies.

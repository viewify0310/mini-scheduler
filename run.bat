@echo off
REM ─────────────────────────────────────────────────────────────────
REM  Launcher for mini_scheduler.py
REM
REM  Edit the line below to point at the pythonw.exe of the Python
REM  environment where you installed customtkinter. Using pythonw.exe
REM  (not python.exe) hides the console window.
REM
REM  Example for an Anaconda env:
REM    set "PYTHONW=C:\Users\YourName\anaconda3\envs\your-env\pythonw.exe"
REM ─────────────────────────────────────────────────────────────────

set "PYTHONW=C:\Users\YourName\anaconda3\envs\your-env\pythonw.exe"

REM %~dp0 is this .bat file's own folder, so the path stays correct
REM no matter where you move the mini-scheduler folder.
start "" "%PYTHONW%" "%~dp0mini_scheduler.py"

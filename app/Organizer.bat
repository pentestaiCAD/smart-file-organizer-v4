@echo off
rem Double-click launcher for the source (dev) version — no console window.
cd /d "%~dp0"
start "" pythonw organizer_gui.py

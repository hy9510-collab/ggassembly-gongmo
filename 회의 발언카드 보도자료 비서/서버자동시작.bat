@echo off
cd /d "%~dp0"
start "speechcard-server" /min .venv\Scripts\python.exe server.py 4600

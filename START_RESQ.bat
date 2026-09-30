@echo off
setlocal
cd /d "%~dp0"
if not exist data mkdir data
if not exist data\voice_notes mkdir data\voice_notes
python app.py
pause

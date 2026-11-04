@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python -c "import av, sounddevice, numpy, scipy" 2>nul || python -m pip install -r requirements.txt
title Geo Radio helper
python georadio_helper.py
pause

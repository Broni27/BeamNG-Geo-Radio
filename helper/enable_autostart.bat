@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python -c "import av, sounddevice, numpy, scipy" 2>nul || python -m pip install -r requirements.txt
python georadio_helper.py --autostart on
echo.
echo Готово: помощник Гео-Радио запущен в фоне и будет стартовать вместе с Windows.
echo Выключить: disable_autostart.bat или галочка в настройках окна Гео-Радио.
timeout /t 6

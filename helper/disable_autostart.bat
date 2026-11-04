@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python georadio_helper.py --autostart off
echo Автозапуск выключен. Уже запущенный помощник остановится при выключении компьютера
echo (или завершите pythonw.exe в диспетчере задач).
timeout /t 6

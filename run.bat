@echo off
title Water Logger Launcher
echo ===================================================
echo  Starting water_logger (Double-Process Architecture)
echo ===================================================
echo.
cd /d %~dp0
echo Launching data collector (src\logger.py)...
start "Water Logger - Collector" cmd /k "python src\logger.py"
echo.
echo Launching FastAPI web server (src\server.py)...
start "Water Logger - Web Server" cmd /k "python src\server.py"
echo.
echo Both processes launched in separate windows!
echo Press any key to exit this launcher window.
pause > nul

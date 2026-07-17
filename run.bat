@echo off
title Water Logger Launcher
echo ===================================================
echo  Starting water_logger (Double-Process Architecture)
echo ===================================================
echo.
echo Launching data collector (logger.py)...
start "Water Logger - Collector" cmd /k "python logger.py"
echo.
echo Launching FastAPI web server (server.py)...
start "Water Logger - Web Server" cmd /k "python server.py"
echo.
echo Both processes launched in separate windows!
echo Press any key to exit this launcher window.
pause > null

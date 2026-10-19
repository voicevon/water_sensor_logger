@echo off
:: 设置控制台为 UTF-8 代码页，防止中文字符和提示信息出现乱码
chcp 65001 >nul
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

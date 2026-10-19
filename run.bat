@echo off
title Water Logger Launcher
echo ===================================================
echo  Starting water_logger (Double-Process Architecture)
echo ===================================================
echo.
cd /d %~dp0

:: 检查并自动释放 8000 端口（防止旧实例残留导致启动绑定失败）
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    echo [Port Conflict] 发现 8000 端口被残留进程 (PID %%a) 占用，正在自动释放...
    taskkill /F /PID %%a >nul 2>&1
)

echo Launching data collector (src\logger.py)...
start "Water Logger - Collector" cmd /k "python src\logger.py"
echo.
echo Launching FastAPI web server (src\server.py)...
start "Water Logger - Web Server" cmd /k "python src\server.py"
echo.
echo Both processes launched in separate windows!
echo Press any key to exit this launcher window.
pause > nul

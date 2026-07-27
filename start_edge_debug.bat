@echo off
echo === 关闭所有 Edge 窗口 ===
taskkill /F /IM msedge.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo === 启动 Edge debug 模式 ===
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222

echo === 等待 Edge 启动 ===
timeout /t 3 /nobreak >nul

echo === 启动 Patent Tool ===
python src/main.py

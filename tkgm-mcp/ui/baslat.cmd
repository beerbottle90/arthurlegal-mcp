@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
title ArthurLegal - Tapu
echo.
echo   ArthurLegal . Tapu -- yerel arayuz baslatiliyor
echo   Tarayici acilmazsa: http://127.0.0.1:8765
echo   Kapatmak icin bu pencerede Ctrl+C.
echo.
python server.py --ui
echo.
echo   Sunucu durdu. Kapatmak icin bir tusa basin.
pause >nul

@echo off
chcp 65001 >nul
title 生图轮椅 - 关闭本窗口即退出
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo 还没有安装：请先双击「安装.cmd」。
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 -s "%~dp0studio\webui.py" %*
if errorlevel 1 pause

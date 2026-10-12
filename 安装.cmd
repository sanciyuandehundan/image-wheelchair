@echo off
chcp 65001 >nul
title 生图轮椅 - 安装
echo 生图轮椅安装：会下载 Python、PyTorch、ComfyUI 和约 32GB 模型，可以随时关掉，重新双击会接着装。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
set code=%errorlevel%
echo.
if %code% neq 0 (
  echo 安装没有完成。看上面的提示处理后，重新双击「安装.cmd」会接着装。
) else (
  echo 安装完成，双击「生图轮椅.cmd」开始使用。
)
pause
exit /b %code%

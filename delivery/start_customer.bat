@echo off
setlocal
set "PYTHONUTF8=1"
where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请安装 Python 3.10 或更高版本，并确保 python 在 PATH 中。
  exit /b 2
)
python "%~dp0..\scripts\customer_launcher.py" %*
exit /b %errorlevel%

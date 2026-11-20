@echo off
rem sf-load-order launcher: runs the tool with the Python on PATH (3.9 or newer). Arguments pass through.
rem Running Python, not only finding it: Windows' Microsoft Store placeholder python.exe is on PATH even when
rem Python isn't installed, and it exits nonzero (9009) without running anything.
python -I -c "import sys; sys.exit(0)" >nul 2>nul
if errorlevel 1 (
  echo RESULT: CANNOT CHECK: Python wasn't found ^(or only the Microsoft Store's placeholder for it^). Fix: install Python 3.9 or newer from https://www.python.org and tick "Add python.exe to PATH", or turn off the python.exe App execution alias in Windows Settings.
  exit /b 2
)
python -I "%~dp0sf-load-order.py" %*
exit /b %errorlevel%

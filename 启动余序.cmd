@echo off
setlocal
py -3.12 -X utf8 -B "%~dp0run_yuxu.py" launch-web --background %*
if errorlevel 1 pause
endlocal

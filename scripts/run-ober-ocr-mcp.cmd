@echo off
setlocal
set "ROOT=%~dp0.."
set "PY_EXE=%ROOT%\server\.venv\Scripts\python.exe"
set "SRV_PY=%ROOT%\server\ober_ocr_mcp.py"
if exist "%PY_EXE%" (
    "%PY_EXE%" "%SRV_PY%" %*
    exit /b %ERRORLEVEL%
)
uv --directory "%ROOT%\server" run --quiet ober-ocr-mcp %*
exit /b %ERRORLEVEL%

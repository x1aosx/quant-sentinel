@echo off
setlocal
set "API_DIR=%~dp0..\api"
set "WEB_DIR=%~dp0..\web"
cd /d "%API_DIR%"

echo ============================================
echo  X-Quant local workbench launcher
echo ============================================

echo [1/3] Checking Python environment...
uv run python -c "import xquant" >nul 2>&1
if errorlevel 1 (
  echo Creating Python environment...
  uv sync --dev
) else (
  echo Python environment ready.
)

if not exist "%WEB_DIR%\node_modules" (
  echo [2/3] Installing web dependencies...
  pushd "%WEB_DIR%"
  call npm install
  popd
) else (
  echo [2/3] Web dependencies ready.
)

echo [3/3] Starting API and web dev server...
start "X-Quant API" /D "%API_DIR%" cmd /k uv run uvicorn xquant.api.app:create_app --factory --reload --port 8000
start "X-Quant Web" /D "%WEB_DIR%" cmd /k npm run dev

echo.
echo API:  http://127.0.0.1:8000/api/v1/health
echo Web:  http://127.0.0.1:5173/
start "" http://127.0.0.1:5173/
echo Close the two console windows to stop the servers.
endlocal

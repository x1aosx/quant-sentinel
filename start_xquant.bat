@echo off
setlocal
cd /d "%~dp0"

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

if not exist "web\node_modules" (
  echo [2/3] Installing web dependencies...
  pushd web
  call npm install
  popd
) else (
  echo [2/3] Web dependencies ready.
)

echo [3/3] Starting API and web dev server...
start "X-Quant API" cmd /k "cd /d ""%~dp0"" && uv run uvicorn xquant.api.app:create_app --factory --reload --port 8000"
start "X-Quant Web" cmd /k "cd /d ""%~dp0web"" && npm run dev"

echo.
echo API:  http://127.0.0.1:8000/api/v1/health
echo Web:  http://127.0.0.1:5173/
start "" http://127.0.0.1:5173/
echo Close the two console windows to stop the servers.
endlocal

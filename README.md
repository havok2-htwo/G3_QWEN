# G3 Qwen TTS

Windows-first Qwen TTS server with:

- FastAPI backend for synthesis, queueing, admin auth, stats, settings, and benchmarks
- React control room with landing page, admin-key gate, model-path control, and benchmark comparisons
- a Qwen runtime adapter that prefers NVIDIA CUDA when available

## Project Layout

- `QWEN_TTS_SERVER_SPEC.md` - product and API specification
- `backend/` - Python service
- `frontend/` - React/Vite dashboard
- `models/` - default local model directory

## Current Status

- Frontend now follows the Genesis/TADA warm-dark control-room style with:
  - landing page on `/`
  - admin gate on `/admin`
  - browser-stored `X-Admin-Key` workflow for the private dashboard
  - updated "G3 QWEN TTS Control Room" / "Powered by SONS" branding
- Frontend builds successfully.
- The backend now exposes `GET /api/health`, `GET/PUT /api/admin/settings`, and `GET/POST /api/admin/keys` in addition to the existing `/v1/...` operator APIs.
- The local `.venv` is wired to CUDA PyTorch and sees the NVIDIA GPU.
- If no Qwen model is present in `models/`, the real synthesis path fails with a clear error until a model is present there or downloads are enabled.

## Quick Install

For a Windows machine, run:

```powershell
.\install.bat
```

That workflow:

- creates `.venv` with Python 3.12
- installs CUDA PyTorch into the local venv
- installs the backend package
- installs frontend dependencies and builds `frontend/dist`

After that, start the full app with:

```powershell
.\start_server.bat
```

Open:

- `http://127.0.0.1:8088/`
- `http://127.0.0.1:8088/admin`

## Frontend

For installed/runtime use, the backend serves the built frontend from `frontend/dist`.

For frontend development:

```powershell
cd frontend
npm install
npm run dev -- --port 5178
```

Production build:

```powershell
cd frontend
npm run build
```

## Backend

Recommended target runtime: Python 3.12.

Create a local environment:

```powershell
uv venv .venv --python 3.12
```

Install CUDA PyTorch into that venv:

```powershell
$env:UV_CACHE_DIR = 'x:\dev\G3_QWEN_TTS\.uv-cache'
uv pip install --python .\.venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cu128 --reinstall torch torchvision torchaudio
```

Install backend dependencies:

```powershell
$env:UV_CACHE_DIR = 'x:\dev\G3_QWEN_TTS\.uv-cache'
cd backend
uv pip install --python ..\.venv\Scripts\python.exe -e .[dev]
```

Start the backend:

```powershell
$env:QWEN_TTS_RUNTIME_BACKEND = 'qwen'
$env:QWEN_TTS_MODELS_ROOT_DIR = 'x:\dev\G3_QWEN_TTS\models'
$env:QWEN_TTS_ALLOW_MODEL_DOWNLOADS = 'false'
& .\.venv\Scripts\python.exe -m qwen_tts_server.main
```

When `frontend/dist` exists, the backend also serves the dashboard on the same port:

- `http://127.0.0.1:8088/`
- `http://127.0.0.1:8088/admin`

If you want the backend to pull models on first use into the configured model directory, set:

```powershell
$env:QWEN_TTS_ALLOW_MODEL_DOWNLOADS = 'true'
```

Default server address:

- `http://127.0.0.1:8088`

## Notes

- The control room stores the admin key locally in the browser and sends it as `X-Admin-Key`.
- The UI can edit the model directory and backend defaults through `Runtime`.
- The backend currently streams PCM by chunking generated audio; the real low-latency quality still depends on what the underlying Qwen runtime exposes.
- `qwen-tts` currently prints a warning if `sox` is not available in `PATH`. That warning is visible in local smoke runs here and should be treated as an ops dependency to verify on the target machine.

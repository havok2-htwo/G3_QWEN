# Qwen TTS Server Backend

FastAPI backend for the local Qwen TTS stack.

## What It Does

- `GET /api/health` for open readiness checks
- `GET /api/admin/keys` and `POST /api/admin/keys` for admin-key metadata and rotation
- `GET /api/admin/settings` and `PUT /api/admin/settings` for control-room runtime settings
- `POST /v1/audio/speech` for direct synthesis
- `POST /v1/jobs` plus job/status endpoints for queued operation
- `GET /v1/settings` and `PUT /v1/settings` for model path and runtime defaults
- `GET /v1/stats` and `GET /v1/stats/gpu` for overview metrics
- `GET /v1/events` for SSE updates
- voice profile storage, API keys, Whisper proxy, and benchmark runs

## Runtime Modes

- `QWEN_TTS_RUNTIME_BACKEND=mock`
  - useful for UI work and fast tests
- `QWEN_TTS_RUNTIME_BACKEND=qwen`
  - uses the real Qwen runtime and prefers CUDA

## Important Environment Variables

- `QWEN_TTS_RUNTIME_BACKEND`
- `QWEN_TTS_MODELS_ROOT_DIR`
- `QWEN_TTS_ALLOW_MODEL_DOWNLOADS`
- `QWEN_TTS_ACTIVE_MODEL`
- `QWEN_TTS_DEFAULT_VOICE`
- `QWEN_TTS_ADMIN_API_KEY`
- `QWEN_TTS_WHISPER_BASE_URL`
- `QWEN_TTS_WHISPER_PATH`

Default model directory:

- `x:\dev\G3_QWEN_TTS\models`

## Local Commands

Install into the project venv:

```powershell
$env:UV_CACHE_DIR = 'x:\dev\G3_QWEN_TTS\.uv-cache'
uv pip install --python ..\.venv\Scripts\python.exe -e .[dev]
```

Run tests:

```powershell
& ..\.venv\Scripts\pytest.exe tests -q -p no:cacheprovider
```

Start the server:

```powershell
$env:QWEN_TTS_RUNTIME_BACKEND = 'qwen'
& ..\.venv\Scripts\python.exe -m qwen_tts_server.main
```

If `frontend/dist` exists, the backend also serves the dashboard at:

- `http://127.0.0.1:8088/`
- `http://127.0.0.1:8088/admin`

## Current Caveats

- The admin dashboard now expects the browser to send `X-Admin-Key`; older client flows using `Authorization: Bearer ...` remain accepted for compatibility.
- Without a model under the configured model directory, the `qwen` backend returns a clear 500 describing which model path is missing.
- CUDA PyTorch must be installed in the local venv or Qwen will report that no CUDA-capable NVIDIA GPU is available.
- `qwen-tts` warns when `sox` is not in `PATH`; verify that on the target Windows machine.

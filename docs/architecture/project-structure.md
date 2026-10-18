# Project Structure

The repository is split by runtime boundary rather than by framework file type.

```text
iMeeting/
├── backend/
│   ├── src/imeeting/
│   │   ├── api/
│   │   │   ├── http/routes/
│   │   │   └── ws/
│   │   ├── engine/
│   │   ├── export/
│   │   ├── llm/
│   │   ├── main.py
│   │   ├── settings.py
│   │   ├── db.py
│   │   ├── models.py
│   │   └── ...
│   ├── migrations/
│   ├── scripts/
│   └── tests/
├── frontend/
│   └── src/
│       ├── app/
│       ├── api/
│       ├── audio/
│       ├── components/
│       ├── pages/
│       └── store/
├── benchmarks/
├── docs/
└── scripts/
```

## Backend

`backend/src/imeeting` is the import root. The `src` layout prevents accidental
imports from the repository root and makes packaging follow the same layout as
development.

| Area | Responsibility |
|---|---|
| `api/http/routes` | HTTP request parsing, authentication dependencies, response DTOs |
| `api/ws` | WebSocket protocol and connection lifecycle |
| `engine` | FunASR model pool, audio I/O, streaming session state machine |
| `llm` | OpenAI-compatible client and minute generation |
| `export` | Subtitle and document rendering |
| `settings`, `db`, `models`, `schemas` | Configuration, persistence and shared domain contracts |

The HTTP and WebSocket adapters may call application modules, but application
modules must not import `api.http.routes` or `api.ws`.

## Frontend

- `src/app` owns application-wide providers, guards, routing and query client setup.
- `src/pages` owns route-level screens.
- `src/components` owns reusable presentation components.
- `src/store` owns shared client state.
- `src/api` owns transport and protocol types.
- `src/audio` owns microphone capture and PCM worklet code.

Route construction is centralized in `src/app/App.tsx`; `src/main.tsx` is only
the browser rendering entry point.

## Verification

```bash
cd backend
python -m ruff check src/imeeting tests
python -m pytest -q

cd ../frontend
npm test
npm run typecheck
npm run build
```

The CI workflow runs the same guardrails on every push and pull request.


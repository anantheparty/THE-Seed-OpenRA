# THE-Seed OpenRA

OpenRA Red Alert live runtime for:

- AI copilot / adjutant interaction
- capability-driven production and task coordination
- deterministic expert execution on top of the game API
- structured logging, replay, and diagnostics

This repository is no longer centered on the old “LLM generates Python and executes it” flow.  
The active runtime is a layered system built around `Adjutant`, `Kernel`, `WorldModel`, `Experts`, and a Vue-based web console.

---

## Active Runtime

Current main path:

```text
Player / Web Console
        |
        v
Adjutant (NLU, routing, queries, coordination)
        |
        v
Kernel (task/job lifecycle, arbitration, unit requests)
        |
        +--> Capability-style control paths
        +--> Optional TaskAgent for complex managed tasks
        |
        v
Execution Experts
        |
        v
OpenRA / OpenCodeAlert / GameAPI
```

Supporting layers:

- `world_model/`: shared state, runtime facts, production queues, event detection
- `logging_system/`: structured logs, per-session persistence, per-task slices
- `ws_server/`: websocket bridge to the frontend
- `web-console-v2/`: active web UI

---

## Quick Start

### 1. Install runtime dependencies

```bash
pip install -r requirements.txt
```

If you use `run.sh`, it will also install the legacy `the-seed` editable package and NLU requirements currently still referenced by the runtime.

### 2. Start the backend

Linux/macOS:

```bash
./run.sh
```

or directly:

```bash
python3 main.py
```

Important runtime flags:

- `--ws-port` (default `8765`)
- `--tick-hz`
- `--actors-refresh-s`
- `--economy-refresh-s`
- `--map-refresh-s`
- `--review-interval`
- `--queue-manager-mode`
- `--enable-voice`

### 3. Start the active web console

```bash
cd web-console-v2
npm install
npm run dev
```

Default ports:

- backend websocket: `ws://127.0.0.1:8765`
- frontend dev server: Vite default (`5173` unless changed)

---

## Main Directories

### Active

- `main.py`
  - runtime entrypoint
- `adjutant/`
  - top-level player-facing coordinator
- `kernel/`
  - deterministic orchestration core
- `task_agent/`
  - bounded LLM-managed task reasoning
- `experts/`
  - execution experts and info/planner helpers
- `world_model/`
  - shared game state and runtime facts
- `openra_api/`
  - Python game control/query layer
- `openra_state/`
  - supporting state datasets and game knowledge inputs
- `ws_server/`
  - websocket server
- `logging_system/`
  - structured logs and persistence
- `web-console-v2/`
  - active frontend
- `tests/`
  - regression and runtime behavior tests
- `docs/`
  - active design notes, audits, roadmap, and reports
- `OpenCodeAlert/`
  - patched game-side code and bridge; this is active and sometimes must be modified

### Active But Transitional

- `nlu_pipeline/`
  - legacy NLU assets reused by the runtime front door
- `queue_manager.py`
  - singleton runtime queue maintenance service
- `unit_registry.py`
  - YAML-backed unit/building registry
- `voice/`
  - optional subsystem; disabled by default unless explicitly enabled

### Deprecated / Legacy / Historical

These paths are not the primary runtime and should not be treated as the current architecture:

- `web-console/`
- `dashboard/`
- `default_runtime/`
- `adapter/`
- `tactical_core/`
- `the_seed/` as a top-level architecture description
- parts of `agents/` that reflect earlier standalone experiments rather than the current runtime

They may still contain useful reference code, but they are not the source of truth for the active system.

---

## Current Project Focus

The project is now past pure feasibility exploration.  
The main focus is to turn the OpenRA runtime into a mature, low-latency, multi-agent coordination system.

Current priorities include:

- making `Adjutant` a stronger battlefield coordinator
- making capability control real for production and prerequisites
- tightening shared-resource and future-unit scheduling
- adding hard unit ownership for battlefield tasks
- expanding the OpenRA action surface so the runtime is no longer weaker than direct Python control
- improving replay, diagnostics, and iteration UX

See:

- `docs/yu/openra_execution_board_20260409.md`
- `docs/yu/archive/20260409/realtime_multiagent_system_roadmap_20260409.md`

---

## Logging and Diagnostics

Every backend run can persist a session log under:

```text
Logs/runtime/session-<timestamp>/
```

Typical contents:

- `all.jsonl`
- `components/<component>.jsonl`
- `tasks/<task_id>.jsonl`
- `session.json`
- benchmark summaries and exports

The frontend diagnostics pane can show current-session task traces and replay recent in-memory history. Offline/session-browser style replay is still an active improvement area.

---

## Notes

- Voice is disabled by default. Enable explicitly with `--enable-voice` or `ENABLE_VOICE=1`.
- Runtime logs and build outputs should be treated as operational artifacts, not hand-edited source content.
- Some top-level docs and older subtrees are still being cleaned up; the files listed above under “Active” are the current runtime truth.

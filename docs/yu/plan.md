## Current
Coordinator/runtime contract cleanup: keep reducing remaining ad hoc coordinator shaping across `adjutant` / `runtime_views` / `task_triage`, now that battlefield capability typing and several stale test surfaces are aligned.

## Queue
Typed runtime/coordinator snapshot follow-up: keep collapsing `main.py` / `adjutant` / `world_model` runtime assembly onto shared typed views instead of ad hoc dict shaping.
Knowledge/planner truth cleanup: keep aligning `experts/knowledge.py` / `experts/planners.py` with the normalized demo capability truth so soft strategy does not overclaim unsupported faction/buildability semantics.
Historical task debug bundle follow-up: keep improving replay/diagnostics so one task can be triaged from structured highlights instead of raw log scrolling.
Test-signal audit follow-up: broader capability/diagnostics E2E coverage still remains, but the bootstrap smoke / adjutant mock-surface gap and several stale assertion surfaces are now closed.
Docs hygiene follow-up: archive stale slice/audit notes in `docs/yu` and keep only actively referenced execution docs at the top level.

## Blocked (optional)

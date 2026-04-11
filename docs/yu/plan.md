## Current
Testing reform / signal quality: layered backend gate, direct-entry startup smoke, E2E classification guardrails, pytest-backed `__main__` runners, live-runner diagnostics consumption, deploy short-circuit truth hardening, live task-surface requirements, shared triage detail, coordinator/base-readiness projection parity, first-pass frontend triage surfacing, and runtime reset on `session_clear`/restart are now in place. The current focus remains the diagnostics/capability yellow zone, but the next slices should stay narrow: keep turning runtime/task/debug surfaces into directly readable “what is blocked, by what, for whom” answers, then lock each visibility contract with tests instead of relying on manual memory.

## Queue
Runtime disconnect degradation: the refresh-storm fix and `session_clear`/restart runtime reset are landed, but higher-level UX/runtime surfaces still flatten disconnect detail to generic “stale”; the next slice should surface the actual last refresh error / failure mode instead of only a boolean.
Outdated backend self-checks: `test_backend.sh` now reflects the current runtime path; any further changes should preserve the layered gate instead of reintroducing a single opaque smoke check.
Frontend task panel collapse: add per-task expand/collapse and default completed-expert/details sections to collapsed so long-lived tasks do not become unreadable; queue this behind the current stability/testing work unless the implementation is trivially isolated.
Capability prompt truth alignment: make capability-facing context distinguish prereq-satisfied from buildable-now, and surface stale-world, disabled-gateway, low-power, and queue-blocked truth as first-class blockers instead of burying them in generic headers.
Unit-reservation contract surfacing: promote `UnitReservation` state from kernel bookkeeping into capability/runtime/debug surfaces, and tighten cancel/fulfill semantics without removing the current fast-path bootstrap path.
Task triage/debug bundle follow-up: expose deterministic `phase` / `waiting_reason` / `blocker` / `highlights` summaries in task list and replay flows so diagnostics answer “what is this task doing now?” before forcing raw log reading.
Knowledge/planner truth cleanup: keep aligning `experts/knowledge.py` / `experts/planners.py` with the normalized demo capability truth so soft strategy does not overclaim unsupported faction/buildability semantics.
Historical task debug bundle follow-up: keep improving replay/diagnostics so one task can be triaged from structured highlights instead of raw log scrolling.
Test-signal audit follow-up: broader capability/diagnostics E2E coverage still remains, but the startup smoke, direct-entry smoke, layered backend gate, bootstrap smoke, E2E classification guardrails, pytest-backed `__main__` runners, and several stale assertion surfaces are now closed.
Docs hygiene follow-up: archive stale slice/audit notes in `docs/yu` and keep only actively referenced execution docs at the top level.

## Blocked (optional)

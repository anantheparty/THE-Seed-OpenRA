## Current
Testing reform / signal quality: layered backend gate, direct-entry startup smoke, E2E classification guardrails, pytest-backed `__main__` runners, live-runner diagnostics consumption, deploy short-circuit truth hardening, and live task-surface requirements are now in place. Shared triage now also surfaces concrete capability blocker/delivery details instead of only generic counts. The current focus is the remaining diagnostics/capability yellow zone: continue turning runtime/task/debug surfaces into directly readable “what is blocked, by what, for whom” answers without forcing raw-log archaeology.

## Queue
Runtime disconnect degradation: the refresh-storm fix is landed, but the remaining work is to audit any higher-level UX/runtime surfaces that still behave poorly once the game disappears mid-session.
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

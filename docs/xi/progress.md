# Xi Agent — Progress Log

## [2026-03-30 00:00] DONE — Agent bootstrap
Initialized docs/xi/ workspace: agents.md, plan.md, progress.md. Checked inbox (empty). Confirmed identity via whoami.

## [2026-03-30 10:27] DONE — Full audit of design docs
Read design.md, test_scenarios.md, implementation_plan.md, code_asset_inventory.md, user_requirements.md.
Wrote comprehensive audit to docs/wang/xi_full_audit.md.
Key findings:
- P0: Test scenarios still use CommandProcessor instead of Adjutant (inconsistency with design decision 19)
- P0: Expert (traditional AI) implementation severely under-specified
- P1: Kernel task 1.3 overloaded, event routing unspecified, no error recovery strategy
- Suggested simplifications: start with single autonomy mode, single task kind, simple Adjutant routing

## [2026-03-30 10:34] DONE — Re-check P0+P1 fixes
Verified wang's fixes across design.md, test_scenarios.md, implementation_plan.md:
- P0-1 FIXED: All 11 test scenarios now use "Adjutant" (T1-T9 step 1, T11 steps 1-2). T9 steps 1-3 skip Adjutant (direct Kernel) — acceptable for concurrent command scenario.
- P0-2 FIXED: Task 3.0a added — Expert design docs as prerequisite, with specific deliverables per Expert type.
- P1-1 FIXED: Kernel split into 1.3a-f (lifecycle/resources/routing/timeout/auto-response/error recovery).
- P1-2 FIXED: Error recovery section added to design.md (LLM timeout/GameAPI disconnect/WorldModel stale/Job exception).
- P1-3 FIXED: Event routing rules specified in Kernel section (actor_id match→Job, discovery→broadcast, strategic→notification only, unmatched→discard).
- Also: xi added to division of labor (看板/Task Agent/语音), test strategy task 3.0b added.

## [2026-03-30 11:12] DONE — Phase 0: Tasks 0.2, 0.3, 0.4
Implemented three Phase 0 tasks:
- 0.3: Created directory skeleton (models/, llm/, kernel/, experts/, world_model/, adjutant/, benchmark/, game_loop/, voice/, tests/) with __init__.py
- 0.2: Data model dataclasses in models/ — Task, Job, ResourceNeed, Constraint, ExpertSignal, Event, NormalizedActor, TaskMessage, PlayerResponse + all enums + ExpertConfig schemas (Recon/Combat/Movement/Deploy/Economy). All models have timestamp fields.
- 0.4: LLM abstraction layer in llm/ — LLMProvider ABC, QwenProvider (DashScope OpenAI-compatible), AnthropicProvider, MockProvider. LLMResponse with text + tool_calls. Streaming interface with fallback.

## [2026-03-30 11:18] DONE — Concept drift fix (decisions 28+29)
Removed AutonomyMode enum and Task.autonomy_mode field. Reduced TaskKind from 4 to 2 (INSTANT/MANAGED only). Commit 861d61d.

## [2026-03-30 11:19] DONE — Audit yu's benchmark (0.5) and cleanup (0.1)
- Benchmark (a325814): 6/6 audit points pass. @timed, span(), query(), export_json(), async support, design decision 27 alignment all verified.
- Cleanup (6735e1e): 5/5 inventory delete files removed, 4 additional legacy files removed (reasonable). Import fix in adapter/openra_env.py correct. Zero residual imports. start-vnc.sh updated consistently.

## [2026-03-30 15:27] DONE — Task 1.4: Task Agent agentic loop
Implemented task_agent/ directory (~300 lines core):
- agent.py: TaskAgent class with full agentic loop (wake→context→multi-turn LLM tool use→sleep), review_interval timer, max_turns limit, LLM retry+timeout, default_if_timeout for decisions
- context.py: ContextPacket builder (task/jobs/world_summary/signals/decisions), all with timestamps
- tools.py: 11 tool definitions (OpenAI format), ToolExecutor with handler registry, benchmark instrumented
- queue.py: AsyncIO-based Signal/Event queue with wake trigger
- tests/test_task_agent.py: 13 tests all passing (context, single-turn, multi-turn, complete_task, max_turns, queues, error handling, full lifecycle, timer, event-in-context, default_if_timeout)
- Audit fixes (ccbf442): Events now in ContextPacket, default_if_timeout executes via patch_job handler, enforcement required in create_constraint. Yu regression audit: zero blockers.

## [2026-03-31 00:00] DONE — Task 2.1: Expert base classes + Job base class
Implemented experts/base.py:
- InformationExpert ABC: analyze(world_state) → derived info
- PlannerExpert ABC: plan(query_type, params, world_state) → proposal
- ExecutionExpert ABC: create_job() factory + generate_job_id()
- BaseJob: full lifecycle (tick/patch/pause/resume/abort), signal emission via callback, resource grant/revoke with auto WAITING/RUNNING transition, constraint reading by scope (global/expert_type/task_id), tick_interval per subclass, benchmark @timed("job_tick"), to_model() for context packets
- tests/test_expert_base.py: 12 tests all passing (incl. abort→resume guard)

## [2026-03-31 00:00] DONE — Task 1.2: GameLoop (10Hz main loop)
Implemented game_loop/loop.py:
- 10Hz asyncio main loop with configurable tick_hz
- Per-tick sequence: WorldModel.refresh() → detect_events() → Kernel.route_events() → Job ticks → dashboard callback
- Job tick scheduler: register/unregister, per-Job tick_interval, skips terminated jobs
- Benchmark instrumented: span("job_tick") per tick
- tests/test_game_loop.py: 7 tests all passing (start/stop, event routing, job scheduling, register/unregister, terminated skip, dashboard callback, configurable tick rate)

## [2026-03-31 00:00] DONE — Task 1.5+1.7: Tool handlers + timestamp propagation
- task_agent/handlers.py: TaskToolHandlers class with 11 handlers wiring to Kernel/WorldModel via Protocol interfaces
- All handler responses include timestamp field (1.7 compliance)
- 1.7 check: all 11 data models + ContextPacket + WorldSummary have timestamp fields
- tests/test_tool_handlers.py: 9 tests (register_all, start_job, lifecycle, complete_task, query_world, cancel_tasks, all_timestamps, constraint_side_effects, end-to-end agent→LLM→handler→Kernel)

## [2026-03-31 00:00] DONE — Task 4.1: Adjutant LLM routing layer
Implemented adjutant/adjutant.py:
- Input classification via LLM (command/reply/query) with JSON response parsing
- Reply routing: matches pending questions by target_message_id or highest priority fallback
- Command routing: Kernel.create_task
- Query handling: LLM + WorldModel.world_summary() → direct answer
- TaskMessage formatting (text mode + card/JSON mode)
- Dialogue history tracking with trim
- Classification failure defaults to command (graceful degradation)
- tests/test_adjutant.py: 10 tests (command, reply, reply_fallback, query, classification_failure, formatting, dialogue_history, notification_formatting, notification_poll_push, notification_no_sink)

## [2026-03-31] DONE — Live integration fixes
- Adjutant error handling: _handle_command try/except, on_command_submit top-level guard
- Query fallback: _rule_based_classify with keyword detection when LLM unavailable
- Expert config schemas added to Task Agent system prompt (5 Expert configs with exact field names)
- ChatView: removed task_update from chat (TaskPanel's responsibility)
- OpsPanel: VNC placeholder (prevent recursive iframe), game control buttons (start/stop/restart)
- State persistence: sync_request on WS connect, localStorage chat history (100 msgs)
- LLM timeout hardening: classification 10s + query 15s timeouts with graceful fallback

## [2026-03-31 00:00] DONE — Task 1.6+1.8: WebSocket backend + review_interval scheduling
- ws_server/server.py: aiohttp WS server, inbound routing (command_submit/cancel/mode_switch), outbound broadcast (6 types), multi-client, JSON+timestamp
- game_loop/loop.py: register_agent/unregister_agent, _check_agent_reviews per tick, initialized last_review_at to now
- tests/test_ws_and_review.py: 7 tests (review wake, register/unregister agent, multi-agent intervals, WS start/stop, inbound, outbound broadcast, multi-client)

# yu Knowledge Base

- Deliverables explicitly assigned by Wang go under `docs/wang/` (or `docs/wang/archive/` for archived investigation artifacts); `docs/yu/` is reserved for yu's own agent state and yu-only files.
- Base `TaskAgent` now has native `push_player_response()` intake that re-injects `PlayerResponse` as a normal wake event; WS / Adjutant reply flow can route through `Kernel.submit_player_response()` directly.
- Test suites that replace the shared runtime surface with custom mocks can hide integration blockers; before clearing a milestone, check the mock API shape against the real `openra_api` / `Kernel` / `WorldModel` contracts.
- `openra_api.GameAPI` is stateless per request, so OpenRA process restarts do not require recreating the API client; runtime reattachment only needs `wait_for_api()` plus `WorldModel.reset_snapshot()` and a forced refresh.
- In the live runtime, `GameLoop` currently runs on the same asyncio loop as WS/Adjutant/LLM coroutines; because `WorldModel.refresh()` and job ticks call synchronous `GameAPI` sockets, slow game polling can starve `asyncio.wait_for()` and make LLM calls appear to hang even when the provider itself is healthy.

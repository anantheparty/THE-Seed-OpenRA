## Current
(empty — register_unit_request full implementation complete)

## Queue
- EconomyCapability auto-creation logic (Kernel creates on first barracks/factory completion)
- Agent suspend/wake: consider asyncio.Event for cleaner blocking (currently 10s timer polling)
- Converge _HINT_TO_UNIT + agent bootstrap aliases with UnitRegistry.resolve_name()
- Expert redesign Phase 1: UnitStatsDB → InfluenceMap → BuildOrderManager (per docs/xi/expert_redesign.md, pending Wang review)

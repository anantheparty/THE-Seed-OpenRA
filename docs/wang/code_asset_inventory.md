# Code Asset Inventory

Date: 2026-03-29
Author: yu

Legend: `keep` = reusable largely as-is, `reference` = mine ideas/data/patterns while rewriting, `rewrite` = important but structurally wrong for the target design, `delete` = legacy/demo/duplicate code not worth carrying forward.

## Overall take

The codebase has three clear strata:

1. Reusable substrate
   - low-level OpenRA API/protocol/data models
   - some utility layers
   - NLU runtime/logging pipeline

2. Valuable reference implementations
   - jobs
   - combat/economy/strategy subsystems
   - tactical experiments
   - duplicate intel stack

3. Legacy or duplicate entrypoints
   - standalone launchers
   - demo runners
   - old facades that no longer match the target architecture

My recommendation is:

- preserve `GameAPI`, protocol models, small helpers, and the NLU runtime tooling
- mine jobs/combat/economy/strategy/tactical code for ideas and data structures
- do not try to evolve the current monolithic runtime into the final architecture

## Main Entry

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `main.py` | 1327 | Runtime entrypoint plus dashboard bridge, enemy agent, strategy bridge, and job loop. | `rewrite` | Critical today, but too monolithic for the new expert architecture. |

## openra_api

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `openra_api/__init__.py` | 32 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_api/action/__init__.py` | 25 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_api/action/attack.py` | 54 | Typed atomic `attack` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/base.py` | 37 | Base action/result abstractions for typed action wrappers. | `keep` | Small reusable foundation if typed actions remain. |
| `openra_api/action/build.py` | 36 | Typed atomic `build` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/camera.py` | 56 | Typed atomic `camera` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/deploy.py` | 35 | Typed atomic `deploy` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/group.py` | 36 | Typed atomic `group` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/move.py` | 74 | Typed atomic `move` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/action/produce.py` | 42 | Typed atomic `produce` action wrapper. | `reference` | Thin verb layer; may be reused under a new executor contract. |
| `openra_api/actor_utils.py` | 101 | Actor selection and classification helpers. | `keep` | Directly reusable utility logic. |
| `openra_api/actor_view.py` | 50 | Read-oriented actor wrapper/helper. | `reference` | Handy helper, but not a strategic architectural anchor. |
| `openra_api/game_api.py` | 1491 | Low-level OpenRA RPC client and protocol surface. | `keep` | Core asset; preserve and test instead of redesigning from scratch. |
| `openra_api/game_midlayer.py` | 85 | Compatibility/demo facade and runnable sample for the old midlayer API. | `delete` | Mostly a legacy compatibility shell plus demo code. |
| `openra_api/intel/__init__.py` | 27 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_api/intel/memory.py` | 24 | `openra_api` intel module: memory. | `reference` | Part of the current `openra_api` intel stack; mine during WorldModel merge. |
| `openra_api/intel/model.py` | 23 | `openra_api` intel module: model. | `reference` | Part of the current `openra_api` intel stack; mine during WorldModel merge. |
| `openra_api/intel/names.py` | 14 | Name normalization helper for units/buildings. | `keep` | Small normalization helper with direct reuse value. |
| `openra_api/intel/rules.py` | 84 | Heuristics and category/value rules for the `openra_api` intel stack. | `reference` | Good rule corpus to carry into the future WorldModel. |
| `openra_api/intel/serializer.py` | 144 | Serializers from intel model to brief/debug payloads. | `reference` | Useful presentation logic, but tied to the old stack shape. |
| `openra_api/intel/service.py` | 826 | Main `openra_api` intel snapshot/cache/service layer. | `reference` | Important source material for the WorldModel merge, but not the final shape. |
| `openra_api/jobs/__init__.py` | 18 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_api/jobs/attack.py` | 190 | Current attack-job implementation. | `rewrite` | Useful behavior substrate, but task/expert semantics need redesign. |
| `openra_api/jobs/base.py` | 97 | Base job contracts, assignments, and tick context. | `reference` | Good substrate patterns for future task/expert runtime. |
| `openra_api/jobs/explore.py` | 483 | Current explore-job implementation. | `rewrite` | Useful exploration logic, but the long-term system should use richer task semantics. |
| `openra_api/jobs/manager.py` | 131 | Job registry and actor-to-job assignment manager. | `reference` | Strong substrate for future runtime ownership/resource binding. |
| `openra_api/jobs/utils.py` | 24 | Small helpers for job movement/placement math. | `reference` | Keep the utilities; redesign the surrounding runtime. |
| `openra_api/jobs_main.py` | 49 | Standalone/demo runner for the old job system. | `delete` | Not needed in the new production architecture. |
| `openra_api/macro_actions.py` | 504 | High-level one-shot macro command facade over `GameAPI`. | `reference` | Good command vocabulary, but execution ownership should move to experts. |
| `openra_api/map_accessor.py` | 38 | Map access helper around API responses. | `keep` | Small reusable utility. |
| `openra_api/models.py` | 179 | Shared datamodels for locations, actors, map/control-point queries. | `keep` | Reusable protocol/data contracts. |
| `openra_api/rts_middle_layer.py` | 37 | Thin facade combining intel service and macro actions. | `reference` | Useful shape, but future kernel should expose WorldModel + experts instead. |
| `openra_api/skill_result.py` | 63 | Small result wrapper for skill/macro execution. | `keep` | Low-risk utility type. |

## agents

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `agents/combat/combat_agent.py` | 548 | Main combat-agent loop for company-level tactical control. | `rewrite` | Valuable combat reference, but too tied to the old LLM/tick structure. |
| `agents/combat/infra/combat_data.py` | 51 | Combat-stat/category lookup data. | `reference` | Reusable domain data for a future combat expert. |
| `agents/combat/infra/dataset_map.py` | 101 | Name/code mapping helpers for combat datasets. | `reference` | Useful support data, not an architectural pillar. |
| `agents/combat/infra/game_client.py` | 189 | Combat-specific game client wrapper. | `reference` | Good API-shaping ideas, but should be collapsed into clearer expert/runtime boundaries. |
| `agents/combat/infra/llm_client.py` | 81 | Combat-specific LLM client wrapper. | `reference` | Likely reusable only as an adapter pattern. |
| `agents/combat/run_standalone.py` | 76 | Standalone combat-agent runner. | `delete` | Dev-only entrypoint, not part of the target runtime. |
| `agents/combat/squad_manager.py` | 202 | Company/squad grouping and assignment logic. | `reference` | Strongly relevant for tactical-method design. |
| `agents/combat/stream_parser.py` | 89 | Parsing/helper logic for streamed combat outputs. | `reference` | Useful helper logic, but not core architecture. |
| `agents/combat/structs.py` | 91 | Combat-side data structures for units/squads/types. | `reference` | Likely worth carrying into a cleaner contract layer. |
| `agents/combat/unit_tracker.py` | 173 | Unit tracking and derived combat-state maintenance. | `reference` | Good pattern source for persistent combat state. |
| `agents/commander.py` | 149 | Legacy builder for SimpleExecutor-based commander. | `delete` | Functionality is duplicated elsewhere and tied to the old flow. |
| `agents/economy/__init__.py` | 0 | Package marker. | `keep` | Harmless packaging glue. |
| `agents/economy/agent.py` | 157 | Economy-agent wrapper around the economy engine/state. | `reference` | Useful patterns, but current agent boundary is not the target contract. |
| `agents/economy/data/combat_data.py` | 101 | Economy-side combat/unit stat lookup table. | `reference` | Reusable dataset/stat table. |
| `agents/economy/data/dataset.py` | 164 | Economy-side unit/building dataset registry. | `reference` | Reusable dataset/stat table. |
| `agents/economy/engine.py` | 497 | Core economy planning/queue/build-order logic. | `reference` | Valuable execution logic, but should be recast as an execution expert. |
| `agents/economy/run_standalone.py` | 76 | Standalone economy runner. | `delete` | Dev-only entrypoint, not core runtime. |
| `agents/economy/state.py` | 222 | Economy state, queues, and production representations. | `reference` | Strong candidate source for future economy expert state. |
| `agents/economy/utils.py` | 133 | Economy unit/faction helpers. | `reference` | Useful supporting logic. |
| `agents/enemy_agent.py` | 618 | Autonomous enemy-side loop with chat, taunts, and action execution. | `reference` | Contains useful control-loop patterns but should be recast as an expert/executor. |
| `agents/nlu_gateway.py` | 908 | Intent routing, rollout, fallback, and NLU execution gateway. | `keep` | Operationally valuable and fairly orthogonal to the expert-system rewrite. |
| `agents/strategy/cli.py` | 171 | Manual CLI for the old strategy stack. | `delete` | Manual/legacy entrypoint rather than target runtime code. |
| `agents/strategy/llm_client.py` | 83 | Strategy-side LLM client adapter. | `reference` | Adapter pattern may survive, but the strategy runtime will not. |
| `agents/strategy/run_strategy.py` | 51 | Standalone strategy runner. | `delete` | Manual/legacy entrypoint rather than target runtime code. |
| `agents/strategy/strategic_agent.py` | 628 | Current strategic agent coordinating strategy/combat/economy/tactical layers. | `rewrite` | Important reference, but structurally not the target architecture. |

## tactical_core

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `tactical_core/__init__.py` | 3 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `tactical_core/client.py` | 81 | Tactical-core API/client glue. | `reference` | Experimental tactical subsystem worth mining, not keeping whole. |
| `tactical_core/constants.py` | 70 | Tactical-core constants/categories. | `reference` | Useful support definitions for extraction. |
| `tactical_core/decision_guard.py` | 115 | Target-management / tactical coordination guard logic. | `reference` | Relevant to future tactical method execution. |
| `tactical_core/enhancer.py` | 286 | Facade coordinating entity manager, decision guard, potential field, and interrupts. | `reference` | Good algorithm bundle to mine; not a direct fit as-is. |
| `tactical_core/entity_manager.py` | 222 | Tactical entity-state manager. | `reference` | Good state-shaping ideas for micro/tactical layers. |
| `tactical_core/interrupt_logic.py` | 187 | Tactical interrupt / emergency override logic. | `reference` | Relevant to expert preemption/abort handling. |
| `tactical_core/launcher.py` | 92 | Standalone launcher for the tactical-core experiment. | `delete` | Dev-only entrypoint for a subsystem likely to be subsumed. |
| `tactical_core/potential_field.py` | 263 | Potential-field micro controller. | `reference` | Highly relevant algorithmically, but should be embedded into the new combat layer. |
| `tactical_core/ui.py` | 96 | Standalone tactical debug/log window. | `delete` | Separate local UI is not aligned with the web-console direction. |

## adapter

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `adapter/openra_env.py` | 176 | Observation adapter from `GameAPI` into executor-friendly environment snapshots. | `reference` | Useful boundary; reshape around WorldModel/expert runtime. |

## openra_state

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `openra_state/__init__.py` | 28 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_state/data/__init__.py` | 13 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_state/data/combat_data.py` | 96 | Alternate intel/data-stack combat data. | `reference` | Duplicate ecosystem; mine useful data definitions only. |
| `openra_state/data/dataset.py` | 210 | Alternate intel/data-stack dataset registry. | `reference` | Duplicate ecosystem; mine useful data definitions only. |
| `openra_state/data/structure_data.py` | 54 | Alternate intel/data-stack structure dataset. | `reference` | Duplicate ecosystem; mine useful data definitions only. |
| `openra_state/intel/__init__.py` | 10 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `openra_state/intel/clustering.py` | 112 | Spatial clustering logic for the alternate intel stack. | `reference` | Good input to the future WorldModel merge. |
| `openra_state/intel/intelligence_service.py` | 157 | Alternate intelligence-service pipeline with zone manager / blackboard updates. | `reference` | Important migration input, but should not remain a parallel stack. |
| `openra_state/intel/zone_manager.py` | 396 | Zone abstraction / map partition logic for the alternate intel stack. | `reference` | Strong candidate for the unified WorldModel spatial layer. |
| `openra_state/visualize_intel.py` | 115 | Standalone intel visualization/debug server. | `delete` | One-off debug surface for the duplicate intel stack. |

## nlu_pipeline

| File | Lines | Purpose | Verdict | Notes |
| --- | ---: | --- | --- | --- |
| `nlu_pipeline/__init__.py` | 1 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `nlu_pipeline/interaction_logger.py` | 46 | Runtime interaction/event logger for dashboard and gateway flows. | `keep` | Operational logging asset; likely still useful. |
| `nlu_pipeline/runtime/__init__.py` | 3 | Package export surface. | `keep` | Keep if the package survives; low maintenance cost. |
| `nlu_pipeline/runtime/intent_runtime.py` | 90 | Portable runtime intent-model wrapper. | `keep` | Reusable runtime boundary for NLU inference. |
| `nlu_pipeline/scripts/__init__.py` | 0 | Package marker. | `keep` | Harmless packaging glue. |
| `nlu_pipeline/scripts/build_annotation_queue.py` | 170 | Build annotation queue for labeling workflows. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/build_dataset.py` | 110 | Assemble NLU training/eval datasets. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/build_unlabeled_pool.py` | 183 | Build candidate unlabeled data pool. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/collect_dashboard_interactions.py` | 119 | Collect dashboard interaction logs into training/eval data. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/collect_hf_dialogue_corpus.py` | 258 | External dialogue corpus collector from HuggingFace sources. | `reference` | Useful data-generation tool, not core runtime code. |
| `nlu_pipeline/scripts/collect_logs.py` | 63 | Extract usable rows from logs. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/collect_online_batch.py` | 435 | Large online batch data-collection / sampling script. | `reference` | Potentially useful, but review before carrying forward. |
| `nlu_pipeline/scripts/collect_web_corpus.py` | 79 | Web text collection script for corpus building. | `reference` | Useful only if the current corpus strategy is retained. |
| `nlu_pipeline/scripts/common.py` | 70 | Shared helper utilities for NLU scripts. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/evaluate.py` | 124 | Evaluate intent models/runtime bundles. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/generate_synthetic.py` | 287 | Synthetic instruction/data generation script. | `reference` | Helpful for experimentation; not part of runtime architecture. |
| `nlu_pipeline/scripts/intent_models.py` | 150 | Intent-model implementations/wrappers. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/metrics.py` | 48 | Metrics helpers for evaluation reporting. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/p0_p1_regression_test.py` | 174 | Regression test harness for early-phase intent behavior. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/prelabel_llm.py` | 245 | LLM-assisted prelabeling utility. | `reference` | Useful workflow tool, but not guaranteed to stay in the long-term pipeline. |
| `nlu_pipeline/scripts/release_bundle.py` | 333 | Build/release an NLU bundle/report artifact. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/rule_weak_labeler.py` | 34 | Weak-rule labeling helper. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/run_smoke.py` | 186 | Smoke-test runner for the NLU/runtime path. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/runtime_auto_rollback.py` | 170 | Runtime rollback helper for NLU deployment safety. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/runtime_metrics.py` | 224 | Runtime metrics aggregation/reporting. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/runtime_runtest.py` | 241 | Runtime test harness for rollout/runtime behavior. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/smoke_runtime_gateway.py` | 139 | Gateway-specific smoke test harness. | `keep` | Operational NLU pipeline utility. |
| `nlu_pipeline/scripts/train_intent.py` | 88 | Intent-model training entrypoint. | `keep` | Operational NLU pipeline utility. |

## Rewrite priorities

If the user wants to reorganize before implementing, I would prioritize this order:

1. `main.py`
   - split runtime orchestration, dashboard transport, enemy runtime, strategy bridge, and job loop
2. strategy/combat/economy integration layer
   - especially `agents/strategy/strategic_agent.py` and the combat/economy agent boundaries
3. unify intel/state stacks
   - absorb the best parts of `openra_api/intel/*` and `openra_state/intel/*` into one `WorldModel`
4. redesign jobs into task/expert execution contracts
   - mine `openra_api/jobs/base.py`, `openra_api/jobs/manager.py`, `tactical_core/potential_field.py`, and squad/state modules
5. delete stale entrypoints and duplicate debug surfaces
   - reduce noise before new implementation starts

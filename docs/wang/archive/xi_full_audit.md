# Xi Full Audit — Fresh-Eyes Review

Author: xi
Date: 2026-03-30
Scope: design.md, test_scenarios.md, implementation_plan.md (+ code_asset_inventory, user_requirements as reference)

---

## 1. Architecture Self-Consistency

Overall: the three-tier Kernel/TaskAgent/Job architecture is clean and well-motivated. The separation of LLM (slow, semantic) from traditional AI (fast, reactive) is the right call. However, I found several internal contradictions from iterative editing.

### 1.1 CommandProcessor vs Adjutant — Not Fully Reconciled

**Problem:** Design.md Section 6 explicitly states "Adjutant 取代 CommandProcessor"（决策 19）. However, **all 11 test scenarios** (T1-T11) still reference "CommandProcessor" in Step 1 as the input handler. T11 ("战况如何?") is especially contradictory — it should be the canonical Adjutant query-routing scenario, but uses "CommandProcessor" throughout.

**Impact:** Anyone implementing from test_scenarios.md will build the wrong component at the entry point.

**Fix:** Replace all "CommandProcessor" references in test_scenarios.md with "Adjutant", and update the routing logic in those steps to match the Adjutant design (classify as new command / reply / query).

### 1.2 Kernel: "无循环被动仲裁" vs GameLoop Ticking

**Status: Consistent, but confusing language.**

user_requirements.md says "Kernel 无循环，被动仲裁". design.md Section 2 shows `GameLoop` ticking the Kernel each frame. These are actually consistent (Kernel has no *independent* loop; it's driven by GameLoop). But the user_requirements phrasing could mislead an implementer.

**Suggestion:** Add a one-line clarification in design.md: "Kernel has no independent thread/loop; it is invoked by GameLoop each tick."

### 1.3 Instant Task — Unclear Lifecycle

"取消探索" (design.md Section 9 edge case) creates a Task of kind=instant. The Instant Task Agent does one LLM call, calls `cancel_tasks`, and completes. But:
- Does Instant Task skip the context packet injection?
- Does Instant Task skip the agentic multi-turn loop (just one LLM call, no sleep)?
- How is `kind=instant` different from a `kind=managed` that just completes quickly?

The 4 kinds (instant/managed/background/constraint) are listed in the data model but their behavioral differences aren't specified. Need a "kind behaviors" table.

### 1.4 Event Routing Relevance — Unspecified

Design says Kernel routes WorldModel Events to "相关 Task Agent 和 Job". Test scenario T1 step 9 shows ENEMY_DISCOVERED routed to ReconJob because actor:57 is involved. But:
- What makes a Job "related" to an event? Is it actor_id matching `Job.resources`? Position proximity? Expert type?
- ENEMY_DISCOVERED doesn't have actor:57 as the actor — it has actor:201 (the enemy). How does Kernel know ReconJob cares?
- BASE_UNDER_ATTACK (T7) is routed by Kernel's pre-registered rules. But who receives ENEMY_EXPANSION? Is it broadcast to all Task Agents?

**Need:** An event routing specification — which events go to which consumers, and by what matching logic.

### 1.5 Signal Routing: fire_and_forget Inconsistency

Design Section 5 says Task Agent in fire_and_forget mode wakes only for `task_complete` and `decision_request`. But T1 step 11 shows `progress` signal being routed but "not waking" the Task Agent. This means the Signal still reaches Kernel — who decides not to wake?

**Need:** Clarify: does Kernel filter Signals by autonomy_mode, or does the Task Agent receive all Signals but only "process" some?

### 1.6 cross-Task State in Context Packets

T6 step 9 says target comes from "context packet 中之前的进攻目标". But context packets are per-Task, injected by Kernel. How does a NEW Task (t6, "修理坦克然后进攻") know about a PREVIOUS attack target?

Options: (a) WorldModel tracks "last known attack target", (b) Adjutant/Kernel adds cross-task context, (c) the LLM infers from world state. This needs to be specified — it's not magic.

---

## 2. Implementation Plan Completeness

### 2.1 Task 1.3 (Kernel) Is Overloaded

Kernel v1 bundles: Task lifecycle, resource allocation, event routing, cancel, pending question timeout, preemption logic, constraint storage, and pre-registered auto-response rules.

This is tagged "大" but should probably be split:
- 1.3a: Task lifecycle (create/destroy/status transitions)
- 1.3b: Resource allocation + preemption
- 1.3c: Event routing
- 1.3d: Pending question timeout
- 1.3e: Pre-registered auto-response rules (BASE_UNDER_ATTACK → auto-create Task)

### 2.2 Missing Tasks

| Gap | Where it should go | Why |
|---|---|---|
| Adjutant → NLU migration | Phase 4 | Design says Adjutant replaces CommandProcessor, but no task for migrating intent classification. Does Adjutant use the existing NLU pipeline or pure LLM? |
| Constraint system implementation | Phase 1 | create_constraint, constraint storage in WorldModel, Job constraint reading — not a standalone task |
| GameAPI integration wiring | Phase 1 | GameAPI is "keep" but needs adapter/wrapper for new Job→GameAPI contract |
| Error recovery strategy | Phase 1 | LLM call failure, GameAPI disconnect, WorldModel refresh failure — no task |
| ResourceNeed resolution algorithm | Phase 1 | The declarative resource model (match predicates, auto-assign) needs its own implementation |
| MovementExpert tick_interval | Phase 3 | Not specified in design (Recon=1s, Combat=0.2s, Economy=5s, Movement=?) |
| DeployExpert tick_interval | Phase 3 | Not specified |
| Test infrastructure | Phase 0 or 1 | How do tests run? Unit tests with mocks? Integration tests needing a live game server? No test strategy |
| Cross-task context in WorldModel | Phase 1 | How does WorldModel provide historical context (e.g., previous attack targets) to new Tasks? |

### 2.3 Dependencies Missing

- Task 1.4 (Task Agent) depends on 0.4 (LLM abstraction) — **correctly listed**, good.
- Task 4.1 (Adjutant) depends on 0.4 — **correctly listed**, good.
- Task 2.1 (Expert base class) should depend on 1.3 (Kernel) because Job lifecycle is managed by Kernel. Currently only depends on 0.2.
- Task 3.0 (BT/FSM/ST framework) is a single "中" task but represents a major design decision. It should be preceded by a research/design task.

### 2.4 xi Not in Division of Labor

Implementation plan lists wang and yu but not xi. Needs update now that I'm joining.

---

## 3. Test Scenario Coverage

### 3.1 Covered Well
- T1-T3: Core Task lifecycle (recon, economy, movement)
- T4: Constraint creation
- T5: Multi-Job coordination (surround)
- T6: Sequential Job chaining (repair → attack)
- T7: Passive event auto-response
- T8: Complex multi-step (build base)
- T9: Concurrent tasks
- T10: System idle behavior
- T11: Query handling

### 3.2 Missing Scenarios

| # | Scenario | Why Important |
|---|---|---|
| T12 | **Resource contention between equal-priority Tasks** | Design only covers high > low. What about 50 vs 50? |
| T13 | **Task Agent LLM failure/timeout** | Job→Brain has default_if_timeout, but what if the initial LLM call to understand intent fails? System hangs? |
| T14 | **Mid-execution player correction** ("不，换个方向进攻") | Player changes mind about an active task. Does this create a new Task and cancel the old one? Or does Adjutant route to the existing Task? |
| T15 | **Constraint enforcement during active combat** | T4 creates the constraint. But no scenario tests a CombatJob actually clamping chase distance mid-fight. |
| T16 | **Cascading resource loss** | Multiple units die simultaneously in combat. Multiple Jobs go to waiting. Kernel tries to reassign from a shrinking pool. |
| T17 | **Cancel all tasks** ("全部停下") | Bulk cancel. How does the Instant Task Agent know to cancel everything? |
| T18 | **Adjutant reply routing with ambiguity** | Player says something that could be a reply OR a new command. Adjutant's LLM confidence threshold? |
| T19 | **Deploy failure** | DeployExpert tries to deploy but position is blocked. What signal? What recovery? |

### 3.3 Test Scenarios Structure Issue

All test scenarios are manual walkthroughs (step-by-step tables). There's no mapping to automated test cases. For Milestone 1 (task 2.3), how do we verify "端到端测试 T1"? Is it:
- A pytest that mocks GameAPI and asserts Signal flow?
- A live test against an OpenRA instance?
- A manual walkthrough?

This needs to be specified or the milestone is unverifiable.

---

## 4. Developer Clarity — "Ready to Code" Gaps

### 4.1 Task Agent Agentic Loop — Need Pseudocode

Design describes the loop conceptually (wake → inject context → LLM → tool_use → ... → sleep). But as the developer who'd implement `task_agent.py`, I need:
- How does the event queue work with asyncio? Is each Task Agent a coroutine?
- How does `review_interval` timer interact with Signal-based waking?
- What's the exact context packet format (JSON schema)?
- What's the system prompt structure? (Design says "system prompt 固定" but doesn't give it.)

### 4.2 Job ↔ Kernel Interface — Callback Mechanism

Design mentions `on_resource_granted([actor_ids])` and `on_resource_lost([actor_ids])` on Jobs. Are these:
- Synchronous method calls from Kernel?
- Signals/events in a queue?
- Direct function calls within the same GameLoop tick?

This affects whether Jobs need to be thread-safe.

### 4.3 WorldModel Query API — Return Types

`query_world` tool lists query_types (my_actors, enemy_bases, economy_status, etc.) but doesn't specify return schemas. The Task Agent LLM needs to know what it's getting back to reason about it.

### 4.4 Which SDK for LLM?

Design says "raw SDK 自建 agentic loop". Implementation plan says "openai / anthropic Python SDK". For Qwen3.5, which provider API? OpenAI-compatible? Dashscope? This affects task 0.4 (LLM abstraction layer).

### 4.5 Macro Actions ↔ GameAPI Relationship

Design says "Macro Actions = GameAPI 工具封装, 不是架构层". But current `macro_actions.py` is 504 lines of high-level commands. In the new architecture, do Jobs call `GameAPI` directly (low-level socket RPC) or through Macro Actions (high-level wrappers)? If directly, those 504 lines of logic need to be re-embedded in each Expert.

---

## 5. Over-Design / Under-Design

### 5.1 Over-Design

**Task Kind System (4 types):**
The distinction between instant/managed/background/constraint adds complexity but may not be necessary at launch. An "instant" task is just a managed task that completes in one LLM turn. A "constraint" task is just a managed task that creates a constraint and completes. Consider: start with a single Task type, and let behavior emerge from the Task Agent's LLM responses. Add kinds later if needed for optimization.

**Adjutant Multi-Question Splitting:**
Section 6 describes Adjutant LLM parsing a player response to split it across multiple pending questions. In practice, RTS players rarely face simultaneous questions. This is complex (LLM must correctly split intent) and fragile (wrong split = wrong action). Simpler: route ambiguous replies to highest-priority question only, and let others timeout. (Design already has this as fallback — make it the primary behavior.)

**Two Autonomy Modes:**
fire_and_forget vs supervised affects which Signals wake the Task Agent. This optimization can come later. Start with supervised (all Signals wake) and add fire_and_forget filtering after measuring actual LLM call overhead.

### 5.2 Under-Design

**Expert Traditional AI — The Core Value:**
This is the project's differentiator ("LLM 赋能传统游戏 AI"), yet Expert implementation is the least specified part. CombatExpert `engagement_mode="surround"` appears in test scenarios, but there's zero description of HOW surrounding works. Implementation plan task 3.3 says "CombatExpert 调研+实现" — but this is the hardest task in the entire project (a real surrounding algorithm with group coordination, position evaluation, timing).

Recommendation: Before Phase 3, write detailed Expert design docs for each Expert type. At minimum:
- CombatExpert: FSM states, engagement mode algorithms, retreat logic
- ReconExpert: Area search strategy, threat avoidance, multi-agent scouting
- MovementExpert: Pathfinding integration, formation movement
- EconomyExpert: Build order logic, resource monitoring, queue management

**WorldModel Event Detection:**
How does WorldModel detect high-level events like FRONTLINE_WEAK or ECONOMY_SURPLUS? These require strategic judgment, not just snapshot diffs. What are the thresholds? Are they configurable?

**Resource Allocation Algorithm:**
"按 ResourceNeed + 优先级" is the design. But the actual algorithm matters:
- When two Jobs want the same actor, does the higher-priority one always win?
- When a Job loses actors, does it immediately try to find replacements, or wait until next tick?
- What if the best-matching actor is far away? Does distance factor in?

**Error Recovery:**
No mention anywhere of what happens when:
- LLM API returns 500/timeout
- GameAPI socket disconnects mid-game
- WorldModel refresh gets stale data
- A Job throws an unhandled exception

---

## 6. Summary

### Strengths
- Clean three-tier architecture with clear separation of concerns
- Brain-cerebellum metaphor is intuitive and well-applied
- Declarative resource model is elegant
- Adjutant as single interaction surface avoids N-to-player chaos
- Test scenarios are detailed and trace system state changes step by step
- Decision log (27 decisions) provides excellent traceability

### Top 5 Issues to Address Before Coding

| Priority | Issue | Section |
|---|---|---|
| **P0** | Test scenarios use CommandProcessor instead of Adjutant | 1.1 |
| **P0** | Expert (traditional AI) implementation is severely under-specified — this is the project's core value | 5.2 |
| **P1** | Task 1.3 Kernel is overloaded, should be split | 2.1 |
| **P1** | No error recovery strategy at any level | 2.2, 5.2 |
| **P1** | Event routing relevance rules are unspecified | 1.4 |

### Top 3 Suggestions

1. **Write Expert design docs before Phase 3.** Each Expert needs its own design document with FSM/BT states, algorithms, and edge cases. This is where the real RTS AI lives.

2. **Specify a test strategy.** Are tests against mocked GameAPI or live game? Define this in Phase 0 so every Phase can produce verifiable tests.

3. **Simplify initial scope.** Start with one autonomy mode (supervised), one Task kind (managed), and simple Adjutant routing (highest-priority question only). Add optimizations after Milestone 1 proves the architecture works end-to-end.

---

*— xi, 2026-03-30*

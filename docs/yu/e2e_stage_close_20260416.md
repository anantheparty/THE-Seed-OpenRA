# 2026-04-16 E2E Stage-Close Register

## Purpose

This document freezes the current state after the latest E2E rounds so the project does not drift again across chat notes, runtime logs, and partial fixes.

It answers four questions:

1. Are the newly exposed failures a drift from the intended design?
2. Do we currently have enough implementation handles to fix them?
3. What must be closed before the next controlled E2E?
4. What can wait for later design / UX work?

## Architecture Baseline

Current design intent is still clear in the live project documents:

- [`README.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/README.md): `Adjutant` is the front door for NLU, routing, queries, and coordination.
- [`PROJECT_STRUCTURE.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/PROJECT_STRUCTURE.md): `Kernel` is deterministic infrastructure, not a planner.
- [`PROJECT_STRUCTURE.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/PROJECT_STRUCTURE.md): `TaskAgent` is bounded and optional for complex managed tasks, not the default brain.
- [`PROJECT_STRUCTURE.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/PROJECT_STRUCTURE.md): Capability is the convergence path for shared production and prerequisite handling.

## Top-Level Judgment

Most current E2E failures are not a fresh architecture rewrite or a proof that the original structure is wrong.

Most of them are implementation-layer drift against the intended structure:

- `Adjutant` is still supposed to be the coordinator, but several direct routes are too fail-open and execute the wrong thing.
- `EconomyCapability` is still supposed to own production and prerequisite truth, but some commands still bypass it or it acts too autonomously.
- `TaskAgent` is still supposed to be bounded, but some vague or mixed commands still fall through into low-value managed tasks.
- UI/runtime truth is still supposed to reflect one coherent state, but some operator surfaces still flicker or describe the wrong execution mode.

So the conclusion is:

- the design has not fundamentally collapsed
- behavior has drifted at the boundaries
- this is still fixable with current code structure

## Do We Have Enough Handles To Fix This?

Yes. The current system already has the necessary control points.

The important implementation handles already exist:

- [`adjutant/adjutant.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/adjutant.py): top-level command routing, overlap checks, reply routing, capability merge, rule paths, task creation.
- [`adjutant/runtime_nlu.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/runtime_nlu.py): fast-path direct routing and composite parsing.
- [`task_triage.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_triage.py): per-task workflow classification and bounded tool surface.
- [`task_agent/policy.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/task_agent/policy.py): capability and managed-task prompt contract.
- [`kernel/`](/Users/kamico/work/theseed/THE-Seed-OpenRA/kernel): deterministic lifecycle, event delivery, unit request handoff, runtime projection.
- [`web-console-v2/`](/Users/kamico/work/theseed/THE-Seed-OpenRA/web-console-v2): operator truth, question actions, voice upload, replay/debug surface.
- [`logs/runtime/`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime): per-session evidence for replaying command intent, routing path, and task drift.

This means the current problem is not “we need a new framework first”.

The current problem is “we need to harden the existing coordinator boundary and stop wrong routes from executing”.

## Router Reality Check

The current router is not yet a clean layered classifier.

It is still primarily a precedence chain inside [`adjutant/adjutant.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/adjutant/adjutant.py): whoever appears earlier in `handle_player_input()` gets first right of refusal.

That matters because the current behavior is shaped as much by ordering as by intent.

Current high-level order is:

1. `ack` shortcut
2. deploy / repair / occupy / attack feedback short-circuits
3. explicit rule commands such as repair / attack / retreat
4. stale-query guard
5. reply routing
6. active-task continuation merge
7. runtime NLU direct routing
8. economy capability merge
9. remaining rule matches
10. LLM classification and command disposition

This is the practical reason many E2E failures feel “low IQ”:

- the wrong early gate can steal a sentence before a better lane sees it
- once some routes match, they fail closed too early
- some other routes fail open too late

There is also a configuration/implementation drift:

- [`nlu_pipeline/configs/runtime_gateway.yaml`](/Users/kamico/work/theseed/THE-Seed-OpenRA/nlu_pipeline/configs/runtime_gateway.yaml) declares more routing switches than the active runtime path actually uses
- so some behavior looks configurable on paper but is effectively decided only by code order and hardcoded acceptance logic

## Evidence Base

Primary runtime evidence came from the latest E2E session:

- [`logs/runtime/session-20260415T194117Z/components/adjutant.jsonl`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T194117Z/components/adjutant.jsonl)
- [`logs/runtime/session-20260415T194117Z/components/dashboard_publish.jsonl`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T194117Z/components/dashboard_publish.jsonl)
- [`logs/runtime/session-20260415T194117Z/tasks/`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T194117Z/tasks)

The most representative task-level drift examples were:

- [`t_234803fb.jsonl`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T194117Z/tasks/t_234803fb.jsonl): reply fallback created a meaningless task from `需要`
- [`t_8331e39b.jsonl`](/Users/kamico/work/theseed/THE-Seed-OpenRA/logs/runtime/session-20260415T194117Z/tasks/t_8331e39b.jsonl): `撤退回基地` drifted into a long managed task before being fixed

## Current Issue Register

### A. Already Fixed In HEAD

These were real E2E problems, but they now have explicit fixes and focused coverage in HEAD. They still need revalidation in the next controlled E2E, but they are no longer open design ambiguity.

1. `撤退回基地` drifting into a managed task
- Classification: implementation drift
- Why it drifted: no dedicated retreat route, so the phrase fell into a generic managed `TaskAgent`
- Current status: fixed
- Closure: retreat-to-base is now a bounded direct movement route instead of an LLM loop

2. `找到敌方基地` being split into a fake production step
- Classification: implementation drift
- Why it drifted: runtime NLU accepted a clause-level `produce` interpretation on a recon goal phrase
- Current status: fixed
- Closure: direct `produce` clause acceptance is now gated by safe production-command heuristics

3. Glued ambiguous production phrases like `建造电厂兵营五个步兵` misbuilding
- Classification: implementation drift
- Why it drifted: direct produce route stayed high-confidence even when one clause contained multiple production aliases
- Current status: fixed
- Closure: contradiction now fails closed and falls back instead of inventing `5 个电厂`

4. Browser voice path uploading malformed raw `webm` and failing ASR
- Classification: implementation drift
- Why it drifted: frontend WAV conversion failure fell back to malformed raw upload too often
- Current status: fixed
- Closure: browser-side recording and ASR upload path were hardened, and auto-send after recognition was restored

5. `需要` replying to a task question but spawning a new command/task
- Classification: implementation drift
- Why it drifted: single short replies were routed too late, after command/continuation paths
- Current status: fixed
- Closure: single pending-question short replies now route before new-command logic

6. Attack-preparation phrases being killed by explicit-target short-circuit
- Classification: implementation drift
- Why it drifted: early attack short-circuit misread friendly unit nouns as enemy target nouns
- Current status: fixed
- Closure: attack preparation and aircraft attack binding were hardened; explicit-target feedback no longer steals these phrases

### B. Urgent Before The Next Controlled E2E

These are the highest-value open issues. They are urgent because they still create obvious product-level wrong behavior, not because they are architecturally deep.

1. EconomyCapability autonomy drift
- Classification: design-aligned but buggy
- Symptom: with no fresh player directive, capability may still start or continue acting in a way that feels like an AI commander
- Why urgent: this directly violates the product promise “AI 副官, not autonomous commander”
- Required closure:
  - no-directive state must be explicit in code
  - planning truth may remain visible
  - execution must not self-start without a directive

2. Mixed-domain routing still fails open
- Classification: implementation drift
- Symptom: one utterance containing production + recon + attack intent can still get split incorrectly or routed to the wrong direct path
- Why urgent: this is the main source of “低智感” and wrong task creation
- Required closure:
  - direct paths should only fire on narrow high-confidence patterns
  - mixed commands should degrade to capability merge, continuation merge, managed task, or clarification
  - wrong execution is worse than slow execution

3. Direct-build fast path is not yet trustworthy enough
- Classification: design-aligned but buggy
- Symptom: short commands like `电厂`, `兵营`, `电厂兵营五个步兵` do not always route cleanly, and sometimes wrong steps are produced
- Why urgent: this is basic operator trust
- Required closure:
  - fast path only for clearly recognized short build commands
  - confidence and contradiction checks must fail closed
  - anything ambiguous should go to capability, not direct execution

4. Continuation / overlap / reply routing still needs a stricter contract
- Classification: implementation drift
- Symptom: a follow-up utterance may merge with the wrong task, refuse the right task, or create a low-value side task
- Why urgent: this breaks conversational control continuity
- Required closure:
  - active-task overlap must be action-domain aware first, noun overlap second
  - pending-question replies must beat new-task creation
  - continuation should prefer task ownership and recent active intent, not just lexical overlap

5. Defense/building ownership boundary is not fully clean
- Classification: implementation drift
- Symptom: commands that should become production or defense-building work can still become a generic managed task that compensates with infantry movement
- Why urgent: this is a hard boundary violation; ordinary tasks should not invent a different domain when they cannot build
- Required closure:
  - defense-building and economy-like commands must merge into capability or cleanly refuse
  - ordinary managed tasks must not “helpfully” replace missing building work with unrelated troop actions

6. Attack-now / prepare-attack / harass / retreat phrasing still needs a unified route contract
- Classification: design-aligned but buggy
- Symptom: “准备进攻”, “骚扰”, “打不过就跑”, “撤退” still have overlapping behavior and inconsistent action strength
- Why urgent: these are common live-control phrases
- Required closure:
  - `prepare` should not immediately fire an attack route
  - `harass` should not become full attack or pure production
  - `retreat` must mean movement away, not only stop attacking

7. Runtime fail-open / fail-closed policy is inconsistent
- Classification: implementation drift
- Symptom: some routes fall through on non-match but hard-stop on execution failure, while others silently degrade to cached summaries or command fallback
- Why urgent: this makes behavior hard to predict and hard to debug
- Required closure:
  - direct route miss is acceptable
  - direct route execution failure should not silently invent another path
  - reply/query/command lanes should each have one explicit degraded mode, not many implicit ones

### C. Important But Can Wait Until After The Next Controlled E2E

These are valid problems, but they should not block the next stage-close if the urgent boundary slices are still open.

1. Persisted replay summary flicker in diagnostics
- Classification: UI/runtime truth issue
- Why later: highly visible but not destructive to core command correctness

2. Task/expert expand-collapse state in the frontend
- Classification: UX improvement
- Why later: useful for readability, but correctness comes first

3. Clarification surface improvements
- Classification: UX improvement
- Examples:
  - better cancel affordance on task questions
  - richer “why this command was not executed” explanation
  - safer clarification buttons

4. Richer mission grammar and composite planning
- Classification: future design work
- Why later: the present priority is fail-closed routing and correct ownership boundaries, not a more ambitious planner

5. More expressive debug/operator surfaces
- Classification: future tooling work
- Why later: logs already give enough signal to fix correctness; the next step is not bigger debug UI, but fewer wrong runtime branches

## What Has Actually Drifted From The Original Design?

The main drift is not “Adjutant vs Commander”.

The main drift is that fast paths and fallback paths are still too loose.

Concrete drift patterns:

- direct routes execute when they should only classify
- managed tasks are created when capability should own the work
- continuation matching is sometimes noun-led instead of intent-led
- capability state is sometimes described as passive while behavior still looks active
- some ordinary tasks still compensate outside their domain when blocked

This is all boundary drift, not a failure of the basic layered design.

## Recommended Simple Stage Close

If the goal is a simple and disciplined stage close now, do exactly this:

1. Freeze this register as the current truth source for the latest E2E.
2. Treat the next implementation round as boundary hardening only, not feature expansion.
3. Close the urgent items in this order:
- EconomyCapability autonomy semantics
- mixed-domain fail-closed routing
- direct-build fast-path contract
- continuation / reply / overlap contract
- attack / prepare / harass / retreat separation
- visible operator truth fixes that remain after the above
4. Run one controlled E2E after those slices are green.
5. Update this register from the new E2E before starting any broader redesign.

## Recommended Routing Target State For This Stage

The next stage does not need a new big framework.

It needs a cleaner lane model.

Use this target split:

1. Control lane
- `cancel`, `reply`, `ack`
- must always run before any command routing

2. Query / info lane
- anything that is clearly asking for state, explanation, or analysis
- once this lane wins, the command router should not keep trying to execute

3. Follow-up lane
- continuation, override, redirect, task-targeted edits
- should be intent-led, not noun-led

4. Direct fast-path lane
- only for low-ambiguity commands that can be fully grounded against runtime truth
- examples: stop attack, bounded retreat, simple recon, explicit repair, explicit visible-target attack

5. Capability lane
- all economic, production, build, tech, expansion, power, and prerequisite work
- short build commands belong here too

6. Managed / LLM lane
- only for cross-domain, ambiguous, strategic, or open-ended requests
- also the fallback for composite commands that cannot be safely direct-routed

## Recommended Working Rule For The Next Round

The next round should use one strict rule:

- every direct route must justify both `why it matched` and `why it is safe to execute immediately`

If a route can explain the first but not the second, it should not execute directly.

It should fall back.

## Bottom Line

Current E2E failures do not show that the project needs a different top-level architecture right now.

They show that the current architecture needs tighter execution contracts.

That is good news:

- the project is not blocked on research
- the codebase already has the right intervention points
- a simple phase-close is possible now
- the next useful work is boundary hardening, not concept proliferation

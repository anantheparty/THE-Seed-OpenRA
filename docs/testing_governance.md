# Testing Governance Plan

## Why This Exists

The current repository has crossed the point where "more tests" automatically means "safer development".
The dominant problem is no longer missing coverage. The dominant problem is **test-system drag**:

- backend mega-files have become large subsystems of their own
- the same behavior is often pinned at multiple layers
- many assertions are surface-shape assertions rather than failure-prevention assertions
- feature work regularly turns into "repair the test stack" work

Current evidence from the repo:

- [`tests/test_ws_and_review.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_ws_and_review.py): about 2.9k lines / 38 tests
- [`tests/test_game_control.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_game_control.py): about 4.6k lines / 51 tests
- extracted owner suites now exist for:
  - [`tests/test_session_browser.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_session_browser.py)
  - [`tests/test_session_history_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_session_history_contract.py)
  - [`tests/test_task_replay_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_task_replay_contract.py)
  - [`tests/test_dashboard_publish_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_dashboard_publish_contract.py)
  - [`tests/test_task_triage_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_task_triage_contract.py)
- last 120 non-merge commits:
  - 112 touched tests
  - 109 touched docs
  - 60 were tests/docs only
- current gate split:
  - [`test_signal_stack.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_signal_stack.sh): narrow fast gate
  - [`test_backend.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_backend.sh): broader backend gate including extracted owner suites

This document defines a governance model to reduce test burden while keeping real safety.

## Simplify, Not Just Split

Splitting mega-files is necessary, but it is not sufficient.

If we only move existing tests into smaller files without reducing overlap, we will keep the same drag with nicer filenames.

The actual policy must be:

- split by ownership
- delete duplicate coverage
- downgrade surface-heavy assertions
- stop adding speculative tests for implausible failures

The repository should optimize for **credible regression prevention**, not for raw assertion volume.

## Goals

1. Reduce development time spent repairing unrelated tests after legitimate behavior changes.
2. Make failures easier to localize.
3. Keep a small, trustworthy green gate for everyday work.
4. Preserve a wider confidence net without forcing it into the inner development loop.

## Non-Goals

- Maximize raw test count.
- Prove every possible surface at every possible layer.
- Keep every historical test just because it once caught something.

## Core Rule: One Truth, One Owner, One Smoke

Each important behavior should have:

- **one primary owner layer**
  - the layer that defines the real semantic contract
- **at most one higher-level smoke**
  - to prove the contract is still wired through the stack

If the same truth is pinned in three or more layers, maintenance cost usually exceeds safety value.

## Core Rule: Every Test Must Defend Against a Real Danger

A test only deserves to exist if it protects against a regression that is both:

- plausible in this codebase
- expensive enough to justify its maintenance cost

If a failure mode is structurally impossible given surrounding code constraints, or would already be caught earlier by a lower-layer owner test, then the higher-layer test should be removed.

## Test Layers

### Layer 1: Truth Tests

Purpose:

- validate pure semantics
- validate state derivation
- validate normalization and policy decisions

Good candidates:

- dataset / capability truth
- `session_browser` resolution and payload assembly
- `task_replay` bundling and historical isolation
- `world_model` pure runtime-fact derivation
- `adjutant` routing / classification
- kernel lifecycle helpers and reservation semantics

Rules:

- no websocket
- no real runtime startup
- no frontend render
- assert semantic outputs, not transport details

### Layer 2: Boundary Contract Tests

Purpose:

- validate module boundaries and transport contracts

Good candidates:

- `WSServer`
- `DashboardPublisher`
- `RuntimeBridge`
- frontend composables and component event wiring

Rules:

- validate one boundary at a time
- assert message type, essential fields, targeting, fail-close behavior
- do not restate the full underlying domain truth from Layer 1

### Layer 3: Runtime Smoke Tests

Purpose:

- prove the main runtime stack is still alive
- prove the most important flows still traverse the real stack

Good candidates:

- `ApplicationRuntime + aiohttp websocket` startup
- one command route for each major family
- reconnect / session clear / restart
- one question-reply and one cancel path

Rules:

- very small number of tests
- no mega-specs
- no broad payload equality
- verify the high-signal outcome only

### Layer 4: Live / Manual Validation

Purpose:

- real match checks
- game-in-loop drift detection
- workflow validation

Good candidates:

- [`tests/test_live_e2e.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_live_e2e.py)
- [`tests/test_live_e2e_runner.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_live_e2e_runner.py)
- [`docs/live_e2e_checklist.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/live_e2e_checklist.md)

Rules:

- not part of the default inner loop
- used before demo, before E2E rounds, and for periodic confidence

## Keep / Delete / Downgrade Rules

### Keep a test if all of these are true

- it guards against a plausible regression
- it has a clear owner layer
- its failure points to a narrow cause
- it protects a contract that matters to player-visible behavior or runtime safety

### Delete or downgrade a test if any of these are true

- it duplicates a lower-layer truth without adding unique signal
- it asserts exact payload shape where only 2-3 fields matter
- it asserts incidental ordering that is not a real product contract
- it checks a scenario that cannot realistically happen after surrounding code constraints
- it exists only because the test harness was once weak
- it protects against a failure mode that no longer has a believable path in the code
- it forces frequent mechanical updates after intentional behavior improvements

### Prefer these assertions

- route selected correctly
- state changed or did not change
- task created or stayed taskless
- requester scoping and no cross-client leak
- fail-close on invalid input
- required field present and meaningful

### Avoid these assertions by default

- full payload dict equality
- exact log dict equality
- exact timestamp values
- long message-order scripts unless order is the contract
- one test proving routing + runtime truth + replay payload + frontend render all together

## Execution Policy

### Inner Loop: feature development

Default loop while coding:

1. run focused owner tests for the theme being changed
2. run `py_compile` or frontend build for touched modules
3. run one nearby smoke only if the change crosses a real boundary

Do **not** run the whole backend gate on every small change.

### Default rule for new feature work

For a normal feature or fix:

- prefer 0-2 new tests
- prefer one owner-layer test
- add one smoke only if the change crosses a real stack boundary

If a change requires touching many unrelated tests, treat that as a signal that the current tests are over-coupled.

### Pre-Commit / Pre-Push

Run a **small hard gate** only.

Recommended composition:

- runtime startup smoke
- one real sync/request transport smoke
- one routing smoke
- one stale/disconnect fail-close smoke
- one session/replay boundary smoke
- one frontend transport smoke
- one frontend control-wiring smoke

Target:

- under a few minutes
- stable enough to trust
- narrow enough to debug quickly

### Branch Milestone

Run a **layered backend gate**:

- all boundary contracts for affected areas
- core runtime invariants
- capability / unit-request mock integrations

This should be broader than the fast gate, but still not equivalent to "run everything".

### Before E2E / Demo / Release

Run:

- live checklist
- manual game-in-loop flow
- selected broader suites if the touched area justifies them

## Immediate Changes For This Repo

### 1. Freeze further growth of the two mega-files

Effective rule:

- do not add new themes to [`tests/test_ws_and_review.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_ws_and_review.py)
- do not add new themes to [`tests/test_game_control.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_game_control.py)

Existing tests in those files may be edited, moved, or deleted.
New tests should go into dedicated files.

### 1.5. Start a deletion-first burn-down

For the next test-governance slices, success is not "more tests moved".
Success is:

- fewer lines in mega-files
- fewer duplicate truths
- smaller fast gate
- fewer commits that only repair tests after legitimate product changes

Concrete default:

- every extraction slice should aim to delete or downgrade at least some redundant assertions
- avoid 1:1 moves unless the moved tests become the unique owner of that truth

### 2. Extract by ownership, not by convenience

Completed extraction targets:

- [`tests/test_session_browser.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_session_browser.py)
  - `default_session_dir`
  - `resolve_session_dir`
  - session catalog / session history payload assembly

- [`tests/test_session_history_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_session_history_contract.py)
  - persisted session-history contract
  - operator / visible history normalization
  - benchmark-record and log-entry exposure

- [`tests/test_task_replay_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_task_replay_contract.py)
  - task replay payload truth
  - historical vs live isolation
  - raw-entry truncation / optional inclusion

- [`tests/test_dashboard_publish_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_dashboard_publish_contract.py)
  - query response publish
  - replay filtering
  - benchmark replay / publish offset behavior
  - publisher fail-open / fail-close behavior

- [`tests/test_task_triage_contract.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_task_triage_contract.py)
  - `build_live_task_payload()` truth
  - capability triage rendering
  - reservation / world-sync / blocker derivation

Remaining likely extraction candidates:

- `RuntimeBridge`-specific task payload builder tests if `tests/test_ws_and_review.py` grows again
- websocket transport-only owner file if bridge/WS contracts become too mixed

### 3. Shrink gate scripts

Current scripts are useful, but they must stay role-focused.

Current structure:

- [`test_signal_stack.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_signal_stack.sh)
  - current smallest trustworthy green line for daily development

- [`test_backend.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_backend.sh)
  - broader backend layered gate

- live checks
  - [`tests/test_live_e2e.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_live_e2e.py)
  - [`docs/live_e2e_checklist.md`](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/live_e2e_checklist.md)

Near-term goal:

- keep `test_signal_stack.sh` small and trustworthy
- keep `test_backend.sh` as the broader owner + runtime gate
- avoid inventing more gate scripts unless a real layer split justifies them

### 4. Reduce selector-based gate definitions

Long `pytest -k "...or ...or ..."` command strings are hard to maintain and hide intent.

Prefer:

- dedicated small files
- dedicated marks for stable layers
- named script sections that map to ownership

Use `-k` only for short-term transition slices.

### 5. Review new tests using a cost/value checklist

Every new test should answer:

1. What regression does this prevent?
2. Why is that regression plausible?
3. Which layer owns this truth?
4. Is there already a lower-layer test covering the same truth?
5. If this fails, will the developer know where to look?

If these questions cannot be answered quickly, the test probably should not exist in its current form.

## Test Reduction Program

### Category A: Keep

- owner-layer semantic tests
- narrow boundary fail-close tests
- a very small number of runtime smokes
- live/manual checks that catch real workflow drift

### Category B: Downgrade

- full payload equality -> essential field assertions
- exact log dict assertions -> selected fields + type/state checks
- sequence-heavy runtime specs -> state convergence checks

### Category C: Delete

- duplicate proofs of the same truth across helper + bridge + runtime smoke + UI
- tests that only pin a transient implementation detail
- tests whose only value was compensating for an old harness hole that is now structurally closed

## Governance Rules For Future PRs

1. No new test may be added to a mega-file unless it is fixing that file's own existing contract.
2. A new high-level smoke requires justification for why a lower-layer owner test is insufficient.
3. If a PR adds more than two new tests for one behavior, it should also identify which older tests become redundant.
4. The burden of proof is on keeping a test, not on deleting it.

## Proposed Default Workflow

### While building a feature

- run only theme-local tests
- run compile/build checks
- optionally run one smoke that crosses the touched boundary

### When the theme is ready

- run `test_fast.sh`

### When a branch reaches a milestone

- run `test_backend.sh`
- run frontend gate if frontend was touched

### Before live demo or E2E round

- run live checklist and manual flows

## Definition of a Good Test in This Repo

A good test here is not the most detailed test.
A good test:

- owns one truth
- fails for one reason
- protects one believable regression
- is cheap to update when behavior intentionally changes

Anything else should be treated as suspect, even if it once looked thorough.

## First Concrete Refactor Slice

Completed governance slices:

1. Extracted session-browser tests out of [`tests/test_ws_and_review.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_ws_and_review.py)
2. Extracted task-replay contract tests out of the same file
3. Extracted dashboard publish owner contracts
4. Extracted task-triage owner contracts
5. Slimmed duplicated runtime smokes in [`tests/test_game_control.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_game_control.py)
6. Narrowed [`test_signal_stack.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_signal_stack.sh) into a true fast gate
7. Included the extracted owner suites in [`test_backend.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_backend.sh), including `test_session_history_contract.py`

Current result:

- mega-file ownership is materially clearer
- fast gate is cheaper and more trustworthy
- broader backend gate no longer silently omits newly extracted owner suites

## First-Wave Deletion / Downgrade Candidates

The following clusters are the highest-value cuts based on the current repo shape, especially the overlap between [`tests/test_ws_and_review.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_ws_and_review.py) and [`tests/test_game_control.py`](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_game_control.py).

### 1. `command_submit` real-runtime route matrix

Problem:

- too many `ApplicationRuntime + websocket` route tests for variants of the same routing truth
- expensive to maintain because every route-contract tweak forces many startup smokes to move together

Keep:

- one smoke for "merge into existing capability task"
- one smoke for "create a new direct task"
- one smoke for "deny or stale-guard stays taskless"

Cut / downgrade:

- the remaining route permutations should move to lower-layer routing tests or one parametrized matrix

### 2. `diagnostics_sync_request` duplicate coverage

Problem:

- the same truth is asserted once at focused bridge level and again through a full real runtime path

Keep:

- the focused bridge-level owner test
- one very thin real runtime smoke that only proves the path reaches the handler and returns diagnostics payloads

Cut / downgrade:

- any real-runtime spec that reasserts the full "fresh baseline, no generic history replay" truth in detail

### 3. `session_clear` overlap

Problem:

- reset semantics, session rotation, requester targeting, and publisher-offset clearing are spread across overlapping tests

Keep:

- one owner test for reset/unregister semantics
- one owner test for publisher or benchmark offset reset
- one thin end-to-end requester-targeting smoke

Cut / downgrade:

- extra tests that restate "rotates session and refreshes baseline" after the owner tests already prove it

### 4. Publisher replay / offset semantics duplicated across bridge and publisher tests

Problem:

- incremental publish vs replay-full-snapshot behavior is already owned by publisher tests
- bridge-level tests restate the same offset semantics

Keep:

- narrow publisher tests as primary owners

Cut / downgrade:

- bridge-level tests that combine incremental publish, replay shape, and benchmark-size boundary in one place unless they prove a truly unique runtime boundary

### 5. Session-root/path validation duplicated at helper and handler layers

Problem:

- `default_session_dir` / `resolve_session_dir` truth is fundamentally helper-level
- handler tests for `session_select` and `task_replay_request` should only prove fail-close transport behavior, not re-prove full path semantics

Keep:

- helper-level tests as truth owners
- one generic handler-level smoke proving invalid session requests fail closed

Cut / downgrade:

- duplicate handler-level invalid-session variants that only restate the same root-resolution rule through different envelopes

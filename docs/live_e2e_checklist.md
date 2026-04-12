# Live E2E Checklist

This is the regular manual validation entry for a real OpenRA match plus the real backend.

It does not replace [`./test_signal_stack.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_signal_stack.sh) or [`./test_backend.sh`](/Users/kamico/work/theseed/THE-Seed-OpenRA/test_backend.sh). Those gates catch fast regressions in the runtime, websocket, frontend, and operator surfaces. This checklist exists for the part the fast gates cannot prove: live game-loop behavior against a real running match.

## Preconditions

1. Start a real match and wait until the local player is fully in-game before starting the backend.
2. Start the backend only after the match is live.
3. Use a fresh session when possible. If the previous run was noisy, clear the session/UI first.
4. For the standard bootstrap path, the local player should still be in an early-game state where `部署基地车 -> 电厂 -> 兵营 -> 矿场 -> 步兵 -> 探索地图 -> 战况如何` is meaningful.

## Standard Phase Order

Run these phases in order:

1. `python3 tests/test_live_e2e.py phase_a`
2. `python3 tests/test_live_e2e.py phase_b`
3. `python3 tests/test_live_e2e.py phase_c`
4. `python3 tests/test_live_e2e.py phase_d`
5. `python3 tests/test_live_e2e.py phase_e`

Running `python3 tests/test_live_e2e.py` executes the full chain, but phase-by-phase runs are easier to triage when the live world is already partially advanced.

## Phase Intent

`phase_a`

- Goal: backend websocket and GameAPI are both reachable.
- Pass: the runner reports a complete websocket baseline plus live self-actor visibility.

`phase_b`

- Goal: early economy/bootstrap actions work on the real map.
- Pass:
  - `部署基地车` creates a construction yard when one is not already present.
  - `建造电厂` increases `powr`.
  - `建造兵营` increases `barr/tent`.
  - `建造矿场` increases `proc`.

`phase_c`

- Goal: the production path can create infantry through the real runtime.
- Pass: `生产3个步兵` increases `e1` count by at least 3.

`phase_d`

- Goal: recon is part of the regular live loop, not an optional side check.
- Pass:
  - `探索地图` returns a task-bearing reply.
  - `runtime_state.active_jobs` exposes a `ReconExpert`.
  - At least one pre-existing scout candidate moves from its baseline position within the observation window.

The runner currently treats these as scout candidates:

- `e1`
- `e3`
- `dog`
- `jeep`
- `ftrk`
- `1tnk`
- `2tnk`
- `3tnk`
- `4tnk`
- `yak`
- `mig`

This is intentionally stricter than the older check that only looked for `ReconExpert` inside `active_jobs`.

`phase_e`

- Goal: pure battlefield query still returns a substantive answer after the earlier live actions.
- Pass: `战况如何？` returns a non-trivial battlefield summary rather than a short generic reply.

## Failure Capture

When a phase fails, keep the failure artifact focused:

1. Save the runner output, especially `recent_debug_context()`.
2. In diagnostics, open the affected task and inspect:
   - `Current Runtime`
   - `task replay`
   - `session_history`
3. Record whether the failure is:
   - route/ack only
   - runtime truth drift
   - expert/job started but no in-game effect
   - task closure/persistence issue

## Interpretation

This checklist is a live validation contract, not a release-green stamp.

- If the fast gates are green but this checklist fails, trust the live failure.
- If `phase_d` fails while earlier bootstrap phases pass, treat that as a recon/runtime behavior problem, not as a generic “E2E is fine” result.
- Before the next serious E2E round, this checklist should be the default entry rather than an ad hoc sequence assembled from memory.

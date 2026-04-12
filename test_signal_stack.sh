#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Runtime entry / control smokes"
python3 -m pytest tests/test_game_control.py -q -k \
  "application_runtime_ws_startup_smoke_and_background_publish \
or test_main_entry_direct_start_smoke_covers_enable_voice_and_task_message_publish \
or test_main_entry_subprocess_short_start_does_not_crash_on_enable_voice \
or application_runtime_ws_degradation_truth_stays_aligned_across_world_snapshot_session_catalog_and_task_replay \
or application_runtime_ws_command_submit_real_adjutant_capability_merge \
or application_runtime_ws_command_submit_runtime_nlu_merge_hits_capability \
or application_runtime_ws_question_reply_round_trip_delivers_to_task_agent \
or application_runtime_ws_command_cancel_round_trip_updates_runtime_truth"

echo
echo "==> Diagnostics / replay truth contracts"
python3 -m pytest tests/test_ws_and_review.py -q -k \
  "sync_request_overlays_live_world_health_into_session_catalog \
or dashboard_publish_fault_is_reflected_in_world_snapshot_runtime_fault_state \
or task_replay_request_returns_persisted_task_log \
or task_replay_request_prefers_live_truth_for_active_task_bundle"

echo
echo "==> Operator surface hints"
(
  cd web-console-v2
  npm test -- --run src/components/__tests__/DiagPanel.spec.js -t \
    "renders selected session world health summary from session_catalog|renders stale and runtime-fault scan hints directly in session selector options|renders session world health context inside replay diagnostics|renders session runtime fault context inside replay diagnostics|renders live unit pipeline focus detail inside the live runtime block|dispatches diagnostics focus event from live unit pipeline focus action"
  npm test -- --run src/components/__tests__/OpsPanel.spec.js -t \
    "aggregates stale, runtime fault, capability truth, and pipeline blockage in the primary status"
)

echo
echo "==> Frontend transport contract"
(
  cd web-console-v2
  npm test -- --run src/composables/__tests__/useWebSocket.spec.js
)

echo
echo "==> Frontend control wiring"
(
  cd web-console-v2
  npm test -- --run src/components/__tests__/ChatView.spec.js -t \
    "sends question_reply from task-question options and disables them after answering|clears chat history on theseed:clear-ui and unregisters websocket handlers on unmount"
  npm test -- --run src/__tests__/App.spec.js -t \
    "requests session_clear first and only clears UI after session_cleared arrives"
  npm test -- --run src/components/__tests__/TaskPanel.spec.js -t \
    "sends command_cancel for a running non-capability task"
)

echo
echo "High-signal runtime/operator gate passed."
echo "This is a fast regression screen for the most important current truths:"
echo "  - real runtime entry + WS publish path"
echo "  - true subprocess script-entry short-start under --enable-voice"
echo "  - live degradation truth parity across snapshot/catalog/replay"
echo "  - deterministic + NLU-routed command_submit / question_reply / command_cancel control routes"
echo "  - live world-health + runtime-fault propagation"
echo "  - replay payload session-context truth"
echo "  - diagnostics session discovery / replay visibility"
echo "  - live unit-pipeline blocking-task visibility and focus jump"
echo "  - primary ops status aggregation across stale/fault/truth/pipeline states"
echo "  - frontend websocket transport contract"
echo "  - frontend control wiring for question_reply / command_cancel / session_clear"
echo
echo "It is intentionally narrow."
echo "Run the broader layered backend gate separately via:"
echo "  ./test_backend.sh"

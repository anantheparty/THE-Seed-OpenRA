#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Runtime entry / control smokes"
python3 -m pytest tests/test_game_control.py -q -k \
  "application_runtime_ws_startup_smoke_and_background_publish \
or test_main_entry_direct_start_smoke_covers_enable_voice_and_task_message_publish \
or test_main_entry_subprocess_short_start_does_not_crash_on_enable_voice \
or test_application_runtime_ws_reconnect_sync_replays_current_baseline \
or test_application_runtime_ws_diagnostics_sync_request_refreshes_baseline_without_replaying_generic_history \
or application_runtime_ws_degradation_truth_stays_aligned_across_world_snapshot_session_catalog_and_task_replay \
or application_runtime_ws_command_submit_real_adjutant_capability_merge \
or application_runtime_ws_command_submit_runtime_nlu_merge_hits_capability \
or application_runtime_ws_command_submit_routes_to_deploy \
or application_runtime_ws_command_submit_deploy_denials_stay_taskless \
or application_runtime_ws_command_submit_stale_guard_stays_taskless \
or application_runtime_ws_command_submit_query_stays_pure_query_path \
or application_runtime_ws_question_reply_round_trip_delivers_to_task_agent \
or application_runtime_ws_question_reply_task_mismatch_preserves_pending_question \
or application_runtime_ws_command_cancel_round_trip_updates_runtime_truth \
or application_runtime_ws_command_cancel_failure_preserves_runtime_truth \
or application_runtime_ws_session_clear_retargets_requesting_client_only \
or application_runtime_ws_game_restart_round_trip \
or application_runtime_ws_game_restart_failure_surfaces_error_and_preserves_runtime_truth"

echo
echo "==> Diagnostics / replay smokes"
python3 -m pytest tests/test_task_replay_contract.py -q -k \
  "task_replay_request_returns_persisted_task_log \
or task_replay_request_prefers_live_truth_for_active_task_bundle"
python3 -m pytest tests/test_session_browser.py -q -k \
  "session_select_returns_catalog_and_task_catalog"

echo
echo "==> Operator surface hints"
(
  cd web-console-v2
  npm test -- --run src/components/__tests__/DiagPanel.spec.js -t \
    "renders structured triage fields inside the replay current-runtime summary|renders session runtime fault context inside replay diagnostics"
  npm test -- --run src/components/__tests__/OpsPanel.spec.js -t \
    "only exposes restart control and emits game_restart|renders disconnect state distinctly from generic stale world status|aggregates stale, runtime fault, capability truth, and pipeline blockage in the primary status"
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
    "renders sent player commands as player-side chat bubbles"
  npm test -- --run src/__tests__/App.spec.js -t \
    "requests session_clear first and only clears UI after session_cleared arrives|notifies backend and refreshes diagnostics when external task focus opens debug mode"
  npm test -- --run src/components/__tests__/TaskPanel.spec.js -t \
    "sends command_cancel for a running non-capability task|renders structured triage metadata chips when present"
)

echo
echo "High-signal runtime/operator gate passed."
echo "This is a fast regression screen for the most important current truths:"
echo "  - real runtime entry + WS publish path"
echo "  - true subprocess script-entry short-start under --enable-voice"
echo "  - live degradation truth reaching operator-visible runtime snapshots"
echo "  - deterministic + NLU-routed command_submit / question_reply / command_cancel control routes"
echo "  - real backend reset/restart round-trips for session_clear and game_restart"
echo "  - minimal replay/session diagnostics path for persisted + live task truth"
echo "  - primary ops status aggregation across stale/fault/truth/pipeline states"
echo "  - player-command chat bubbles + app/task panel operator wiring truth"
echo "  - frontend websocket transport contract"
echo "  - frontend control wiring for question_reply / command_cancel / session_clear / diagnostics late-open sync"
echo
echo "It is intentionally narrow and cheap enough for frequent local use."
echo "Run the broader layered backend gate separately via:"
echo "  ./test_backend.sh"

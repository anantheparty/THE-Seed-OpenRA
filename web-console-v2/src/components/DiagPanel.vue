<template>
  <div class="diag-panel">
    <h3>Diagnostics</h3>
    <div v-if="worldSyncStale" class="triage-summary world-sync-summary">
      <div class="triage-status">世界状态同步异常</div>
      <div class="triage-meta">
        <span>world=stale</span>
        <span v-if="worldSyncFailures">failures={{ worldSyncFailures }}<template v-if="worldSyncFailureThreshold">/{{ worldSyncFailureThreshold }}</template></span>
        <span v-if="worldSyncFailureThreshold">threshold={{ worldSyncFailureThreshold }}</span>
        <span v-if="worldSyncError">error={{ worldSyncError }}</span>
      </div>
    </div>
    <div v-if="capabilityTruthBlocker" class="triage-summary truth-summary">
      <div class="triage-status">能力真值受限</div>
      <div class="triage-meta">
        <span>blocker={{ capabilityTruthBlocker }}</span>
        <span v-if="capabilityTruthFaction">faction={{ capabilityTruthFaction }}</span>
        <span>demo capability roster 未覆盖当前阵营</span>
      </div>
    </div>
    <div v-if="runtimeFaultState.degraded" class="triage-summary runtime-fault-summary">
      <div class="triage-status">运行时降级</div>
      <div class="triage-meta">
        <span v-if="runtimeFaultState.count > 1">count={{ runtimeFaultState.count }}</span>
        <span v-if="runtimeFaultState.first_at && runtimeFaultState.first_at !== runtimeFaultState.updated_at">
          since={{ formatSessionFaultTime(runtimeFaultState.first_at) }}
        </span>
        <span v-if="runtimeFaultState.updated_at">at={{ formatSessionFaultTime(runtimeFaultState.updated_at) }}</span>
        <span>source={{ runtimeFaultState.source }}</span>
        <span v-if="runtimeFaultState.stage">stage={{ runtimeFaultState.stage }}</span>
        <span v-if="runtimeFaultState.error">error={{ runtimeFaultState.error }}</span>
      </div>
    </div>
    <div v-if="liveRuntimeSummary" class="triage-summary runtime-summary">
      <div class="triage-status">Live Runtime</div>
      <div class="triage-meta">
        <span v-if="liveRuntimeSummary.capabilityTaskLabel">cap={{ liveRuntimeSummary.capabilityTaskLabel }}</span>
        <span v-if="liveRuntimeSummary.capabilityPhase">cap_phase={{ liveRuntimeSummary.capabilityPhase }}</span>
        <span v-if="liveRuntimeSummary.capabilityBlocker">cap_blocker={{ liveRuntimeSummary.capabilityBlocker }}</span>
        <span>tasks={{ liveRuntimeSummary.activeTaskCount }}</span>
        <span>jobs={{ liveRuntimeSummary.activeJobCount }}</span>
        <span>req={{ liveRuntimeSummary.unfulfilledRequestCount }}</span>
        <span>res={{ liveRuntimeSummary.reservationCount }}</span>
        <span v-if="liveRuntimeSummary.selectedTaskLabel">selected={{ liveRuntimeSummary.selectedTaskLabel }}</span>
        <span v-if="liveRuntimeSummary.selectedTaskGroupSize">group={{ liveRuntimeSummary.selectedTaskGroupSize }}</span>
        <span v-if="liveRuntimeSummary.selectedTaskActors">actors={{ liveRuntimeSummary.selectedTaskActors }}</span>
      </div>
      <div v-if="liveRuntimeSummary.unitPipelinePreview" class="replay-overview">
        {{ liveRuntimeSummary.unitPipelinePreview }}
      </div>
      <div
        v-if="liveRuntimeSummary.unitPipelineFocusDetail || liveRuntimeSummary.unitPipelineFocusRequestCount || liveRuntimeSummary.unitPipelineFocusReservationCount"
        class="triage-meta"
      >
        <span v-if="liveRuntimeSummary.unitPipelineFocusRequestCount">
          focus_req={{ liveRuntimeSummary.unitPipelineFocusRequestCount }}
        </span>
        <span v-if="liveRuntimeSummary.unitPipelineFocusReservationCount">
          focus_res={{ liveRuntimeSummary.unitPipelineFocusReservationCount }}
        </span>
        <span v-if="liveRuntimeSummary.unitPipelineFocusDetail">
          focus={{ liveRuntimeSummary.unitPipelineFocusTaskLabel ? `#${liveRuntimeSummary.unitPipelineFocusTaskLabel} · ${liveRuntimeSummary.unitPipelineFocusDetail}` : liveRuntimeSummary.unitPipelineFocusDetail }}
        </span>
        <button
          v-if="liveRuntimeSummary.unitPipelineFocusTaskId"
          type="button"
          class="filter-btn replay-tag session-highlight-btn"
          @click="jumpToDiagnosticsTask(liveRuntimeSummary.unitPipelineFocusTaskId)"
        >
          定位到阻塞任务
        </button>
      </div>
    </div>
    <div class="trace-controls">
      <label class="trace-label" for="session-select">Session</label>
      <div class="trace-session-row">
        <select id="session-select" v-model="selectedSessionDir" class="trace-select">
          <option v-for="session in sessionCatalog" :key="session.session_dir" :value="session.session_dir">
            {{ formatSessionOption(session) }}
          </option>
        </select>
        <button type="button" class="filter-btn" @click="refreshDiagnostics">刷新</button>
      </div>
      <div v-if="selectedSessionMeta" class="task-log-path" :title="selectedSessionMeta.session_dir">
        🗂 {{ selectedSessionMeta.session_name }} · tasks={{ selectedSessionMeta.task_count }} · records={{ selectedSessionMeta.record_count }}
      </div>
      <div v-if="selectedSessionTaskRollup" class="triage-meta session-task-rollup">
        <span>task_summary</span>
        <span>non_terminal={{ selectedSessionTaskRollup.nonTerminal }}</span>
        <span>terminal={{ selectedSessionTaskRollup.terminal }}</span>
        <span v-for="item in selectedSessionTaskRollup.statuses" :key="`session-rollup-${item.status}`">
          {{ item.status }}={{ item.count }}
        </span>
      </div>
      <div v-if="selectedSessionHighlights.length" class="replay-tags session-highlights">
        <button
          v-for="item in selectedSessionHighlights"
          :key="`session-highlight-${item.taskId}`"
          type="button"
          class="filter-btn replay-tag session-highlight-btn"
          @click="focusDiagnosticsTask(item.taskId)"
        >
          {{ item.label }} · {{ item.detail }}
        </button>
      </div>
      <div
        v-if="selectedSessionWorldHealth"
        class="triage-meta session-health"
        :title="selectedSessionWorldHealth.last_error_detail || selectedSessionWorldHealth.last_error || ''"
      >
        <span>{{ formatSessionHealthStatus(selectedSessionWorldHealth) }}</span>
        <span v-if="selectedSessionWorldHealth.max_consecutive_failures">
          sync_fail=max {{ selectedSessionWorldHealth.max_consecutive_failures }}<template v-if="selectedSessionWorldHealth.failure_threshold">/{{ selectedSessionWorldHealth.failure_threshold }}</template>
        </span>
        <span v-if="selectedSessionWorldHealth.stale_refreshes">
          stale_refresh={{ selectedSessionWorldHealth.stale_refreshes }}
        </span>
        <span v-if="selectedSessionWorldHealth.slow_events">
          slow={{ selectedSessionWorldHealth.slow_events }}
        </span>
        <span v-if="selectedSessionWorldHealth.max_totalMs">
          max_refresh={{ selectedSessionWorldHealth.max_totalMs }}ms
        </span>
        <span v-if="selectedSessionWorldHealth.last_failure_layer">
          layer={{ selectedSessionWorldHealth.last_failure_layer }}
        </span>
        <span v-if="selectedSessionWorldHealth.last_error">
          last={{ formatSessionHealthError(selectedSessionWorldHealth.last_error) }}
        </span>
        <span v-if="selectedSessionWorldHealth.last_error_detail">
          detail={{ formatSessionHealthError(selectedSessionWorldHealth.last_error_detail) }}
        </span>
      </div>
      <div
        v-if="selectedSessionRuntimeFault"
        class="triage-meta session-health session-runtime-fault"
        :title="selectedSessionRuntimeFault.error || ''"
      >
        <span>runtime_fault=seen</span>
        <span v-if="selectedSessionRuntimeFault.count > 1">count={{ selectedSessionRuntimeFault.count }}</span>
        <span
          v-if="selectedSessionRuntimeFault.first_at && selectedSessionRuntimeFault.first_at !== selectedSessionRuntimeFault.updated_at"
        >
          since={{ formatSessionFaultTime(selectedSessionRuntimeFault.first_at) }}
        </span>
        <span v-if="selectedSessionRuntimeFault.updated_at">
          at={{ formatSessionFaultTime(selectedSessionRuntimeFault.updated_at) }}
        </span>
        <span>source={{ selectedSessionRuntimeFault.source }}</span>
        <span v-if="selectedSessionRuntimeFault.stage">stage={{ selectedSessionRuntimeFault.stage }}</span>
        <span v-if="selectedSessionRuntimeFault.error">error={{ formatSessionHealthError(selectedSessionRuntimeFault.error) }}</span>
        <span
          v-for="item in selectedSessionRuntimeFault.breakdown"
          :key="`session-fault-breakdown-${item.source}-${item.stage}`"
        >
          {{ formatSessionFaultBreakdownItem(item) }}
        </span>
      </div>
      <div v-if="displayedOperatorMessages.length" class="replay-section session-operator-strip">
        <div class="replay-heading">Operator Surface</div>
        <div
          v-for="item in displayedOperatorMessages"
          :key="item.key"
          class="triage-meta session-operator-row"
        >
          <span>{{ item.label }}@{{ formatSessionFaultTime(item.timestamp) }}</span>
          <span v-if="item.taskLabel">task=#{{ item.taskLabel }}</span>
          <span>{{ item.message }}</span>
          <button
            v-if="item.taskId"
            type="button"
            class="filter-btn replay-tag session-highlight-btn"
            @click="focusDiagnosticsTask(item.taskId)"
          >
            定位
          </button>
        </div>
      </div>
      <label class="trace-label" for="task-trace-select">Task Trace</label>
      <select id="task-trace-select" v-model="selectedTaskId" class="trace-select">
        <option value="ALL">全部任务</option>
        <option v-for="task in activeTaskCatalog" :key="task.task_id" :value="task.task_id">
          {{ formatTaskOption(task) }}
        </option>
      </select>
      <div v-if="selectedTaskLogPath" class="task-log-path" :title="selectedTaskLogPath">
        📄 {{ selectedTaskLogPath }}
      </div>
      <div v-if="selectedTaskCatalogSummary" class="triage-meta selected-task-meta">
        <span>status={{ selectedTaskCatalogSummary.status }}</span>
        <span v-if="selectedTaskCatalogSummary.entryCount">entries={{ selectedTaskCatalogSummary.entryCount }}</span>
        <span v-if="selectedTaskCatalogSummary.summary">summary={{ selectedTaskCatalogSummary.summary }}</span>
      </div>
    </div>
    <div v-if="selectedTaskTriage" class="triage-summary">
      <div class="triage-status">{{ selectedTaskTriage.status_line }}</div>
      <div class="triage-meta">
        <span>state={{ selectedTaskTriage.state }}</span>
        <span v-if="selectedTaskTriage.phase">phase={{ selectedTaskTriage.phase }}</span>
        <span v-if="selectedTaskTriage.waiting_reason">waiting={{ selectedTaskTriage.waiting_reason }}</span>
        <span v-if="selectedTaskTriage.blocking_reason">blocker={{ selectedTaskTriage.blocking_reason }}</span>
        <span v-if="selectedTaskTriage.reservation_ids?.length">reservations={{ selectedTaskTriage.reservation_ids.length }}</span>
        <span v-if="selectedTaskTriage.reservation_preview">reservation={{ selectedTaskTriage.reservation_preview }}</span>
        <span v-if="selectedTaskTriage.active_expert">expert={{ selectedTaskTriage.active_expert }}</span>
        <span v-if="selectedTaskTriage.active_group_size">group={{ selectedTaskTriage.active_group_size }}</span>
        <span v-if="selectedTaskTriage.world_stale">world=stale</span>
        <span v-if="selectedTaskTriage.world_sync_failures">
          sync_fail={{ selectedTaskTriage.world_sync_failures }}<template v-if="selectedTaskTriage.world_sync_failure_threshold">/{{ selectedTaskTriage.world_sync_failure_threshold }}</template>
        </span>
        <span v-if="selectedTaskTriage.world_sync_error">sync={{ selectedTaskTriage.world_sync_error }}</span>
      </div>
    </div>
    <div v-if="selectedTaskReplayBundle" class="replay-summary">
      <div class="replay-title">Persisted Replay Summary</div>
      <div class="replay-overview">{{ selectedTaskReplayBundle.summary }}</div>
      <div class="triage-meta">
        <span>entries={{ selectedTaskReplayBundle.entry_count }}</span>
        <span>duration={{ selectedTaskReplayBundle.duration_s }}s</span>
        <span v-if="selectedTaskReplayBundle.last_transition">
          last={{ selectedTaskReplayBundle.last_transition.label }}
        </span>
      </div>
      <div v-if="selectedTaskReplaySessionWorldHealth" class="replay-section">
        <div class="replay-heading">Session Health</div>
        <div class="triage-meta">
          <span>{{ formatSessionHealthStatus(selectedTaskReplaySessionWorldHealth) }}</span>
          <span v-if="selectedTaskReplaySessionWorldHealth.max_consecutive_failures">
            sync_fail=max {{ selectedTaskReplaySessionWorldHealth.max_consecutive_failures }}<template v-if="selectedTaskReplaySessionWorldHealth.failure_threshold">/{{ selectedTaskReplaySessionWorldHealth.failure_threshold }}</template>
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.stale_refreshes">
            stale_refresh={{ selectedTaskReplaySessionWorldHealth.stale_refreshes }}
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.slow_events">
            slow={{ selectedTaskReplaySessionWorldHealth.slow_events }}
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.max_totalMs">
            max_refresh={{ selectedTaskReplaySessionWorldHealth.max_totalMs }}ms
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.last_failure_layer">
            layer={{ selectedTaskReplaySessionWorldHealth.last_failure_layer }}
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.last_error">
            last={{ formatSessionHealthError(selectedTaskReplaySessionWorldHealth.last_error) }}
          </span>
          <span v-if="selectedTaskReplaySessionWorldHealth.last_error_detail">
            detail={{ formatSessionHealthError(selectedTaskReplaySessionWorldHealth.last_error_detail) }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplaySessionRuntimeFault" class="replay-section">
        <div class="replay-heading">Session Runtime Fault</div>
        <div class="triage-meta">
          <span>runtime_fault=seen</span>
          <span v-if="selectedTaskReplaySessionRuntimeFault.count > 1">
            count={{ selectedTaskReplaySessionRuntimeFault.count }}
          </span>
          <span
            v-if="selectedTaskReplaySessionRuntimeFault.first_at && selectedTaskReplaySessionRuntimeFault.first_at !== selectedTaskReplaySessionRuntimeFault.updated_at"
          >
            since={{ formatSessionFaultTime(selectedTaskReplaySessionRuntimeFault.first_at) }}
          </span>
          <span v-if="selectedTaskReplaySessionRuntimeFault.updated_at">
            at={{ formatSessionFaultTime(selectedTaskReplaySessionRuntimeFault.updated_at) }}
          </span>
          <span>source={{ selectedTaskReplaySessionRuntimeFault.source }}</span>
          <span v-if="selectedTaskReplaySessionRuntimeFault.stage">
            stage={{ selectedTaskReplaySessionRuntimeFault.stage }}
          </span>
          <span v-if="selectedTaskReplaySessionRuntimeFault.error">
            error={{ formatSessionHealthError(selectedTaskReplaySessionRuntimeFault.error) }}
          </span>
          <span
            v-for="item in selectedTaskReplaySessionRuntimeFault.breakdown"
            :key="`replay-fault-breakdown-${item.source}-${item.stage}`"
          >
            {{ formatSessionFaultBreakdownItem(item) }}
          </span>
        </div>
      </div>
      <div v-if="selectedReplayTriage" class="replay-section">
        <div class="replay-heading">{{ selectedTaskReplayBundle.current_runtime?.triage ? 'Current Runtime' : 'Replay Triage' }}</div>
        <div class="replay-overview">{{ selectedReplayTriage.status_line }}</div>
        <div class="triage-meta">
          <span>state={{ selectedReplayTriage.state }}</span>
          <span v-if="selectedReplayTriage.phase">
            phase={{ selectedReplayTriage.phase }}
          </span>
          <span v-if="selectedReplayTriage.waiting_reason">
            waiting={{ selectedReplayTriage.waiting_reason }}
          </span>
          <span v-if="selectedReplayTriage.blocking_reason">
            blocker={{ selectedReplayTriage.blocking_reason }}
          </span>
          <span v-if="selectedReplayTriage.reservation_ids?.length">
            reservations={{ selectedReplayTriage.reservation_ids.length }}
          </span>
          <span v-if="selectedReplayTriage.reservation_preview">
            reservation={{ selectedReplayTriage.reservation_preview }}
          </span>
          <span v-if="selectedReplayTriage.active_expert">
            expert={{ selectedReplayTriage.active_expert }}
          </span>
          <span v-if="selectedReplayTriage.world_sync_failures">
            sync_fail={{ selectedReplayTriage.world_sync_failures }}<template v-if="selectedReplayTriage.world_sync_failure_threshold">/{{ selectedReplayTriage.world_sync_failure_threshold }}</template>
          </span>
          <span v-if="selectedReplayTriage.world_sync_error">
            sync={{ selectedReplayTriage.world_sync_error }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.capability_truth" class="replay-section">
        <div class="replay-heading">Capability Truth</div>
        <div class="triage-meta">
          <span v-if="selectedTaskReplayBundle.capability_truth.truth_blocker">
            blocker={{ selectedTaskReplayBundle.capability_truth.truth_blocker }}
          </span>
          <span v-if="selectedTaskReplayBundle.capability_truth.faction">
            faction={{ selectedTaskReplayBundle.capability_truth.faction }}
          </span>
          <span v-if="selectedTaskReplayBundle.capability_truth.base_status">
            base={{ selectedTaskReplayBundle.capability_truth.base_status }}
          </span>
          <span v-if="selectedTaskReplayBundle.capability_truth.next_unit_type">
            next={{ selectedTaskReplayBundle.capability_truth.next_unit_type }}
          </span>
          <span v-if="selectedTaskReplayBundle.capability_truth.blocking_reason">
            block_reason={{ selectedTaskReplayBundle.capability_truth.blocking_reason }}
          </span>
          <span v-if="selectedTaskReplayBundle.capability_truth.buildable_now">
            buildable_now=true
          </span>
        </div>
        <div v-if="selectedTaskReplayBundle.capability_truth.issue_now?.length" class="replay-tags">
          <span
            v-for="item in selectedTaskReplayBundle.capability_truth.issue_now"
            :key="`issue-now-${item}`"
            class="replay-tag"
          >
            issue={{ item }}
          </span>
        </div>
        <div v-if="selectedTaskReplayBundle.capability_truth.blocked_now?.length" class="replay-tags">
          <span
            v-for="item in selectedTaskReplayBundle.capability_truth.blocked_now"
            :key="`blocked-now-${item}`"
            class="replay-tag"
          >
            blocked={{ item }}
          </span>
        </div>
        <div v-if="selectedTaskReplayBundle.capability_truth.ready_items?.length" class="replay-tags">
          <span
            v-for="item in selectedTaskReplayBundle.capability_truth.ready_items"
            :key="`ready-item-${item}`"
            class="replay-tag"
          >
            ready={{ item }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.llm?.rounds || selectedTaskReplayBundle.llm?.failures" class="replay-section">
        <div class="replay-heading">LLM</div>
        <div class="triage-meta">
          <span>rounds={{ selectedTaskReplayBundle.llm.rounds }}</span>
          <span>failures={{ selectedTaskReplayBundle.llm.failures }}</span>
          <span>prompt={{ selectedTaskReplayBundle.llm.prompt_tokens }}</span>
          <span>completion={{ selectedTaskReplayBundle.llm.completion_tokens }}</span>
          <span>tool_rounds={{ selectedTaskReplayBundle.llm.tool_rounds }}</span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.debug?.latest_context || selectedTaskReplayBundle.debug?.latest_llm_input" class="replay-section">
        <div class="replay-heading">Debug Snapshot</div>
        <div v-if="selectedTaskReplayBundle.debug?.latest_context" class="triage-meta">
          <span>ctx.jobs={{ selectedTaskReplayBundle.debug.latest_context.job_count }}</span>
          <span>ctx.signals={{ selectedTaskReplayBundle.debug.latest_context.signal_count }}</span>
          <span>ctx.events={{ selectedTaskReplayBundle.debug.latest_context.event_count }}</span>
          <span>ctx.other={{ selectedTaskReplayBundle.debug.latest_context.other_task_count }}</span>
          <span>ctx.decisions={{ selectedTaskReplayBundle.debug.latest_context.open_decision_count }}</span>
        </div>
        <div v-if="selectedTaskReplayBundle.debug?.latest_context?.runtime_fact_keys?.length" class="replay-tags">
          <span
            v-for="key in selectedTaskReplayBundle.debug.latest_context.runtime_fact_keys"
            :key="`rf-${key}`"
            class="replay-tag"
          >
            {{ key }}
          </span>
        </div>
      <div v-if="selectedTaskReplayBundle.debug?.latest_llm_input" class="triage-meta">
          <span>llm.messages={{ selectedTaskReplayBundle.debug.latest_llm_input.message_count }}</span>
          <span>llm.tools={{ selectedTaskReplayBundle.debug.latest_llm_input.tool_count }}</span>
          <span v-if="selectedTaskReplayBundle.debug.latest_llm_input.wake">
            wake={{ selectedTaskReplayBundle.debug.latest_llm_input.wake }}
          </span>
          <span v-if="selectedTaskReplayBundle.debug.latest_llm_input.attempt">
            attempt={{ selectedTaskReplayBundle.debug.latest_llm_input.attempt }}
          </span>
        </div>
      </div>
      <details v-if="selectedTaskReplayBundle.llm_turns?.length" class="replay-detail">
        <summary>LLM Turns · {{ selectedTaskReplayBundle.llm_turns.length }}</summary>
        <div
          v-for="turn in selectedTaskReplayBundle.llm_turns"
          :key="`llm-turn-${turn.turn_index}`"
          class="replay-detail-card"
        >
          <div class="triage-meta">
            <span>turn={{ turn.turn_index }}</span>
            <span v-if="turn.wake">wake={{ turn.wake }}</span>
            <span v-if="turn.attempt">attempt={{ turn.attempt }}</span>
            <span>status={{ turn.status }}</span>
          </div>
          <div v-if="turn.response_text" class="replay-overview">{{ turn.response_text }}</div>
          <div v-if="turn.reasoning_content" class="replay-item replay-blocker">{{ turn.reasoning_content }}</div>
          <div v-if="turn.error" class="replay-item replay-blocker">{{ turn.error }}</div>
          <pre v-if="turn.context_packet" class="trace-details">{{ formatJsonBlock(turn.context_packet) }}</pre>
          <pre v-if="turn.input_messages?.length" class="trace-details">{{ formatJsonBlock(turn.input_messages) }}</pre>
          <pre v-if="turn.tool_calls_detail?.length" class="trace-details">{{ formatJsonBlock(turn.tool_calls_detail) }}</pre>
        </div>
      </details>
      <details v-if="selectedTaskReplayBundle.expert_runs?.length" class="replay-detail">
        <summary>Expert Runs · {{ selectedTaskReplayBundle.expert_runs.length }}</summary>
        <div
          v-for="run in selectedTaskReplayBundle.expert_runs"
          :key="`expert-run-${run.job_id}`"
          class="replay-detail-card"
        >
          <div class="triage-meta">
            <span>{{ run.job_id }}</span>
            <span v-if="run.expert_type">expert={{ run.expert_type }}</span>
            <span v-if="run.started_elapsed_s !== null">t={{ run.started_elapsed_s }}s</span>
            <span>signals={{ run.signals?.length || 0 }}</span>
          </div>
          <div v-if="run.latest_signal" class="replay-overview">{{ formatReplayItem(run.latest_signal) }}</div>
          <pre v-if="run.config" class="trace-details">{{ formatJsonBlock(run.config) }}</pre>
          <pre v-if="run.signals?.length" class="trace-details">{{ formatJsonBlock(run.signals) }}</pre>
          <pre v-if="run.tool_results?.length" class="trace-details">{{ formatJsonBlock(run.tool_results) }}</pre>
        </div>
      </details>
      <details v-if="selectedTaskReplayBundle.lifecycle_events?.length" class="replay-detail">
        <summary>Lifecycle · {{ selectedTaskReplayBundle.lifecycle_events.length }}</summary>
        <pre class="trace-details">{{ formatJsonBlock(selectedTaskReplayBundle.lifecycle_events) }}</pre>
      </details>
      <details
        v-if="selectedTaskReplayBundle.unit_pipeline?.unfulfilled_requests?.length || selectedTaskReplayBundle.unit_pipeline?.unit_reservations?.length"
        class="replay-detail"
      >
        <summary>
          Unit Pipeline · req={{ selectedTaskReplayBundle.unit_pipeline?.unfulfilled_requests?.length || 0 }}
          · res={{ selectedTaskReplayBundle.unit_pipeline?.unit_reservations?.length || 0 }}
        </summary>
        <div v-if="selectedTaskReplayBundle.unit_pipeline?.unfulfilled_requests?.length" class="replay-detail-card">
          <div class="replay-heading">Requests</div>
          <pre class="trace-details">{{ formatJsonBlock(selectedTaskReplayBundle.unit_pipeline.unfulfilled_requests) }}</pre>
        </div>
        <div v-if="selectedTaskReplayBundle.unit_pipeline?.unit_reservations?.length" class="replay-detail-card">
          <div class="replay-heading">Reservations</div>
          <pre class="trace-details">{{ formatJsonBlock(selectedTaskReplayBundle.unit_pipeline.unit_reservations) }}</pre>
        </div>
      </details>
      <div v-if="selectedTaskReplayBundle.tools?.length" class="replay-section">
        <div class="replay-heading">Tools</div>
        <div class="replay-tags">
          <span v-for="item in selectedTaskReplayBundle.tools" :key="`tool-${item.name}`" class="replay-tag">
            {{ item.name }} × {{ item.count }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.experts?.length" class="replay-section">
        <div class="replay-heading">Experts</div>
        <div class="replay-tags">
          <span v-for="item in selectedTaskReplayBundle.experts" :key="`expert-${item.name}`" class="replay-tag">
            {{ item.name }} × {{ item.count }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.signals?.length" class="replay-section">
        <div class="replay-heading">Signals</div>
        <div class="replay-tags">
          <span v-for="item in selectedTaskReplayBundle.signals" :key="`signal-${item.name}`" class="replay-tag">
            {{ item.name }} × {{ item.count }}
          </span>
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.blockers?.length" class="replay-section">
        <div class="replay-heading">Blockers</div>
        <div
          v-for="(item, idx) in selectedTaskReplayBundle.blockers"
          :key="`blocker-${idx}`"
          class="replay-item replay-blocker"
        >
          {{ formatReplayItem(item) }}
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.highlights?.length" class="replay-section">
        <div class="replay-heading">Highlights</div>
        <div
          v-for="(item, idx) in selectedTaskReplayBundle.highlights"
          :key="`highlight-${idx}`"
          class="replay-item"
        >
          {{ formatReplayItem(item) }}
        </div>
      </div>
      <div v-if="selectedTaskReplayBundle.player_visible?.length" class="replay-section">
        <div class="replay-heading">Player Visible</div>
        <div
          v-for="(item, idx) in selectedTaskReplayBundle.player_visible"
          :key="`player-visible-${idx}`"
          class="replay-item"
        >
          {{ formatReplayItem(item) }}
        </div>
      </div>
      <button
        v-if="selectedTaskReplayCount > 0"
        type="button"
        class="filter-btn replay-toggle"
        @click="toggleRawReplay(selectedTaskId)"
      >
        {{
          selectedTaskRawReplayVisible
            ? '隐藏原始回放'
            : `展开原始回放 (${selectedTaskReplayCountLabel})`
        }}
      </button>
    </div>
    <div class="trace-stream">
      <div v-for="(entry, i) in filteredTraceEntries" :key="`trace-${i}`" class="trace-entry">
        <span class="log-time">{{ formatTime(entry.timestamp) }}</span>
        <span class="trace-source">[{{ entry.source }}]</span>
        <span v-if="entry.taskLabel" class="trace-task">{{ entry.taskLabel }}</span>
        <span v-if="entry.jobId" class="trace-job">{{ entry.jobId }}</span>
        <span class="trace-msg">{{ entry.message }}</span>
        <span v-if="entry.repeatCount > 1" class="trace-repeat">×{{ entry.repeatCount }}</span>
        <pre v-if="entry.details" class="trace-details">{{ formatTraceDetails(entry.details) }}</pre>
      </div>
      <div v-if="!filteredTraceEntries.length" class="empty">当前没有可追踪的任务事件</div>
    </div>

    <div class="log-filter">
      <button
        v-for="lvl in ['ALL', 'INFO', 'WARN', 'ERROR']"
        :key="lvl"
        :class="['filter-btn', { active: filterLevel === lvl }]"
        @click="filterLevel = lvl"
      >{{ lvl }}</button>
    </div>
    <div class="log-filter">
      <button
        v-for="component in COMPONENT_FILTERS"
        :key="component"
        :class="['filter-btn', { active: filterComponent === component }]"
        @click="filterComponent = component"
      >{{ component }}</button>
    </div>
    <div class="log-stream" ref="logEl">
      <div v-for="(entry, i) in filteredLogs" :key="i" :class="['log-entry', entry.level?.toLowerCase()]">
        <span class="log-time">{{ formatTime(entry.timestamp) }}</span>
        <span class="log-component">[{{ entry.component || 'log' }}]</span>
        <span v-if="entry.tag && entry.tag !== entry.component" class="log-tag">[{{ entry.tag }}]</span>
        <span class="log-msg">{{ entry.message }}</span>
      </div>
      <div v-if="!filteredLogs.length" class="empty">等待日志...</div>
    </div>

    <h3>Benchmark</h3>
    <div class="benchmark-summary">
      <div v-for="entry in displayedBenchmarks" :key="entry.tag" class="bench-row">
        <span class="bench-tag">{{ entry.tag }}</span>
        <span class="bench-count">{{ entry.stats.count }}次</span>
        <span class="bench-avg">avg {{ entry.stats.avg.toFixed(1) }}ms</span>
        <span class="bench-max">max {{ entry.stats.max.toFixed(1) }}ms</span>
      </div>
      <div v-if="benchmarkOverflowCount > 0" class="bench-note">
        仅显示 top {{ BENCHMARK_LIMIT }} tags，已隐藏 {{ benchmarkOverflowCount }} 项
      </div>
      <div v-if="!displayedBenchmarks.length" class="empty">无数据</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, reactive, onMounted, onUnmounted, watch } from 'vue'
import {
  formatTaskLabel,
  registerTaskLabel,
  registerTaskLabels,
  replaceTaskIdsWithLabels,
} from '../composables/taskLabels.js'

const props = defineProps({ on: Function, send: Function })

const BENCHMARK_LIMIT = 20
const EXPANDED_TRACE_LIMIT = 1000
const REPLAY_REFRESH_DEBOUNCE_MS = 1000
const COMPONENT_FILTERS = ['ALL', 'adjutant', 'task_agent', 'kernel', 'expert', 'world_model', 'game_loop']
const LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARN: 2, WARNING: 2, ERROR: 3 }
const TERMINAL_TASK_STATUS = new Set(['succeeded', 'failed', 'aborted', 'partial'])

const logEntries = ref([])
const logEl = ref(null)
const benchmarkStats = reactive({})
const worldSyncStale = ref(false)
const worldSyncFailures = ref(0)
const worldSyncFailureThreshold = ref(0)
const worldSyncError = ref('')
const capabilityTruthBlocker = ref('')
const capabilityTruthFaction = ref('')
const runtimeFaultState = ref({
  degraded: false,
  source: '',
  stage: '',
  error: '',
  count: 0,
  first_at: 0,
  updated_at: 0,
})
const liveRuntimeState = ref({})
const liveUnitPipelinePreview = ref('')
const liveUnitPipelineFocus = ref({
  detail: '',
  taskId: '',
  taskLabel: '',
  requestCount: 0,
  reservationCount: 0,
})
const filterLevel = ref('ALL')
const filterComponent = ref('ALL')
const selectedTaskId = ref('ALL')
const liveTaskCatalog = ref([])
const sessionCatalog = ref([])
const sessionTaskCatalog = ref([])
const selectedSessionDir = ref('')
const traceEntries = ref([])
const operatorMessages = ref([])
const replayCache = reactive({})
const replayBundleCache = reactive({})
const replayMetaCache = reactive({})
const replayRequestedLevel = reactive({})
const replayExpanded = reactive({})
const replayRefreshTimers = new Map()
let lastWorldTruthSignature = ''

const currentSessionDir = computed(() =>
  sessionCatalog.value.find((item) => item.is_current)?.session_dir
  || sessionCatalog.value.find((item) => item.is_latest)?.session_dir
  || ''
)

const selectedSessionMeta = computed(() =>
  sessionCatalog.value.find((item) => item.session_dir === selectedSessionDir.value) || null
)

const selectedSessionWorldHealth = computed(() =>
  normalizeSessionWorldHealth(selectedSessionMeta.value?.world_health || null)
)

const selectedSessionRuntimeFault = computed(() =>
  normalizeSessionRuntimeFault(selectedSessionMeta.value?.runtime_fault_summary || null)
)

const selectedSessionTaskRollup = computed(() =>
  normalizeSessionTaskRollup(selectedSessionMeta.value?.task_rollup || null)
)

const selectedSessionHighlights = computed(() => {
  if (!selectedSessionMeta.value) return []
  return activeTaskCatalog.value
    .map((task) => {
      const detail = compactSingleLine(taskOptionDetail(task), 56)
      if (!detail) return null
      const status = String(task?.status || '').toLowerCase()
      let severity = 0
      if (status === 'failed') severity = 4
      else if (status === 'partial') severity = 3
      else if (status === 'aborted') severity = 2
      else if (task?.triage?.blocking_reason || task?.triage?.waiting_reason) severity = 1
      else if (!['succeeded', 'running'].includes(status)) severity = 1
      if (severity <= 0) return null
      return {
        taskId: task.task_id,
        label: compactSingleLine(task.label || formatTaskLabel(task.task_id), 12) || '任务',
        detail,
        severity,
        timestamp: Number(task.timestamp || task.created_at || 0),
      }
    })
    .filter(Boolean)
    .sort((left, right) => {
      if (right.severity !== left.severity) return right.severity - left.severity
      return right.timestamp - left.timestamp
    })
    .slice(0, 3)
})

const displayedOperatorMessages = computed(() => (
  [...operatorMessages.value]
    .filter((item) => {
      if (!selectedTaskId.value || selectedTaskId.value === 'ALL') return true
      return !item.taskId || item.taskId === selectedTaskId.value
    })
    .sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0))
    .slice(0, 6)
))

const activeTaskCatalog = computed(() => {
  if (!selectedSessionDir.value) return [...liveTaskCatalog.value]
  if (selectedSessionDir.value !== currentSessionDir.value) return [...sessionTaskCatalog.value]
  return mergeCatalogTasks(sessionTaskCatalog.value, liveTaskCatalog.value)
})

const filteredLogs = computed(() => {
  const minLevel = filterLevel.value === 'ALL' ? 0 : (LEVEL_ORDER[filterLevel.value] || 0)
  return logEntries.value.filter((entry) => {
    const entryLevel = LEVEL_ORDER[(entry.level || '').toUpperCase()] || 0
    if (entryLevel < minLevel) return false
    if (filterComponent.value === 'ALL') return true
    return entry.component === filterComponent.value
  })
})

const filteredTraceEntries = computed(() => {
  const replayKey = replayCacheKey(selectedTaskId.value)
  const items = selectedTaskId.value === 'ALL'
    ? traceEntries.value
    : (
      replayExpanded[replayKey]
        ? mergeTraceEntries(
            replayCache[replayKey] || [],
            traceEntries.value.filter((entry) => entry.taskId === selectedTaskId.value),
          )
        : traceEntries.value.filter((entry) => entry.taskId === selectedTaskId.value)
    )
  if (selectedTaskId.value !== 'ALL' && replayExpanded[replayKey]) {
    return compactTraceEntries(items.slice(-EXPANDED_TRACE_LIMIT))
  }
  return compactTraceEntries(items.slice(-200))
})

const selectedTaskCatalogEntry = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  return activeTaskCatalog.value.find((task) => task.task_id === selectedTaskId.value) || null
})

const selectedTaskLogPath = computed(() => {
  return selectedTaskCatalogEntry.value?.log_path || null
})

const selectedTaskCatalogSummary = computed(() => {
  const task = selectedTaskCatalogEntry.value
  if (!task) return null
  const status = compactSingleLine(task.status || '', 24)
  const summary = compactSingleLine(task.summary || '', 72)
  const entryCount = Number(task.entry_count || 0)
  if (!status && !summary && !entryCount) return null
  return {
    status,
    summary,
    entryCount,
  }
})

const selectedTaskTriage = computed(() => {
  return selectedTaskCatalogEntry.value?.triage || null
})

const selectedTaskReplayBundle = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  return replayBundleCache[replayCacheKey(selectedTaskId.value)] || null
})

const selectedTaskReplaySessionWorldHealth = computed(() =>
  normalizeSessionWorldHealth(selectedTaskReplayBundle.value?.session_context?.world_health || null)
)

const selectedTaskReplaySessionRuntimeFault = computed(() =>
  normalizeSessionRuntimeFault(selectedTaskReplayBundle.value?.session_context?.runtime_fault_summary || null)
)

const selectedReplayTriage = computed(() => {
  const bundle = selectedTaskReplayBundle.value
  if (!bundle) return null
  return bundle.current_runtime?.triage || bundle.replay_triage || null
})

const selectedTaskReplayCount = computed(() => {
  if (selectedTaskId.value === 'ALL') return 0
  const key = replayCacheKey(selectedTaskId.value)
  return Number(replayMetaCache[key]?.raw_entry_count || 0)
})

const selectedTaskReplayCountLabel = computed(() => {
  const rawCount = selectedTaskReplayCount.value
  const totalCount = Number(selectedTaskReplayBundle.value?.entry_count || 0)
  if (!rawCount) return '0'
  if (!totalCount || rawCount >= totalCount) return String(rawCount)
  return `${rawCount}/${totalCount}`
})

const selectedTaskRawReplayVisible = computed(() => {
  if (selectedTaskId.value === 'ALL') return false
  return Boolean(replayExpanded[replayCacheKey(selectedTaskId.value)])
})

const displayedBenchmarks = computed(() =>
  Object.entries(benchmarkStats)
    .sort(([, left], [, right]) => {
      if (right.count !== left.count) return right.count - left.count
      return right.max - left.max
    })
    .slice(0, BENCHMARK_LIMIT)
    .map(([tag, stats]) => ({ tag, stats }))
)

const benchmarkOverflowCount = computed(() =>
  Math.max(Object.keys(benchmarkStats).length - BENCHMARK_LIMIT, 0)
)

const liveRuntimeSummary = computed(() => {
  const runtimeState = liveRuntimeState.value || {}
  const activeTasks = runtimeState?.active_tasks && typeof runtimeState.active_tasks === 'object'
    ? runtimeState.active_tasks
    : {}
  const activeJobs = runtimeState?.active_jobs && typeof runtimeState.active_jobs === 'object'
    ? runtimeState.active_jobs
    : {}
  const unfulfilledRequests = Array.isArray(runtimeState?.unfulfilled_requests)
    ? runtimeState.unfulfilled_requests
    : []
  const reservations = Array.isArray(runtimeState?.unit_reservations)
    ? runtimeState.unit_reservations
    : []
  const capabilityStatus = runtimeState?.capability_status && typeof runtimeState.capability_status === 'object'
    ? runtimeState.capability_status
    : {}
  const selectedTaskRuntime = selectedTaskId.value !== 'ALL'
    ? activeTasks[selectedTaskId.value] || null
    : null
  const selectedActorIds = Array.isArray(selectedTaskRuntime?.active_actor_ids)
    ? selectedTaskRuntime.active_actor_ids
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item))
    : []

  const summary = {
    capabilityTaskLabel: String(capabilityStatus.task_label || ''),
    capabilityPhase: String(capabilityStatus.phase || ''),
    capabilityBlocker: String(capabilityStatus.blocker || ''),
    activeTaskCount: Object.keys(activeTasks).length,
    activeJobCount: Object.keys(activeJobs).length,
    unfulfilledRequestCount: unfulfilledRequests.length,
    reservationCount: reservations.length,
    selectedTaskLabel: selectedTaskRuntime?.label || (selectedTaskId.value !== 'ALL' ? formatTaskLabel(selectedTaskId.value) : ''),
    selectedTaskGroupSize: Number(selectedTaskRuntime?.active_group_size || 0),
    selectedTaskActors: formatActorPreview(selectedActorIds),
    unitPipelinePreview: String(liveUnitPipelinePreview.value || ''),
    unitPipelineFocusDetail: String(liveUnitPipelineFocus.value.detail || ''),
    unitPipelineFocusTaskId: String(liveUnitPipelineFocus.value.taskId || ''),
    unitPipelineFocusTaskLabel: String(liveUnitPipelineFocus.value.taskLabel || ''),
    unitPipelineFocusRequestCount: Number(liveUnitPipelineFocus.value.requestCount || 0),
    unitPipelineFocusReservationCount: Number(liveUnitPipelineFocus.value.reservationCount || 0),
  }

  if (
    !summary.capabilityTaskLabel
    && !summary.capabilityPhase
    && !summary.capabilityBlocker
    && !summary.activeTaskCount
    && !summary.activeJobCount
    && !summary.unfulfilledRequestCount
    && !summary.reservationCount
    && !summary.selectedTaskGroupSize
    && !summary.selectedTaskActors
    && !summary.unitPipelinePreview
    && !summary.unitPipelineFocusDetail
    && !summary.unitPipelineFocusRequestCount
    && !summary.unitPipelineFocusReservationCount
  ) {
    return null
  }
  return summary
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString()
}

function normalizeCatalogTasks(tasks) {
  registerTaskLabels(tasks)
  return [...(tasks || [])]
    .sort((a, b) => Number(b?.timestamp || b?.created_at || 0) - Number(a?.timestamp || a?.created_at || 0))
    .map((task) => ({
      ...task,
      label: formatTaskLabel(task.task_id),
      log_path: task.log_path || null,
    }))
}

function mergeCatalogTasks(baseTasks, overlayTasks) {
  const merged = new Map()
  for (const task of baseTasks || []) {
    if (!task?.task_id) continue
    merged.set(task.task_id, { ...task })
  }
  for (const task of overlayTasks || []) {
    if (!task?.task_id) continue
    merged.set(task.task_id, {
      ...(merged.get(task.task_id) || {}),
      ...task,
    })
  }
  return normalizeCatalogTasks([...merged.values()])
}

function setLiveTaskCatalog(tasks) {
  liveTaskCatalog.value = normalizeCatalogTasks(tasks)
}

function mergeLiveTask(task) {
  if (!task?.task_id) return
  registerTaskLabel(task.task_id)
  const idx = liveTaskCatalog.value.findIndex((item) => item.task_id === task.task_id)
  const next = {
    ...(idx >= 0 ? liveTaskCatalog.value[idx] : {}),
    ...task,
    label: formatTaskLabel(task.task_id),
    log_path: task.log_path || (idx >= 0 ? liveTaskCatalog.value[idx]?.log_path : null) || null,
  }
  const nextItems = [...liveTaskCatalog.value]
  if (idx >= 0) nextItems.splice(idx, 1, next)
  else nextItems.push(next)
  liveTaskCatalog.value = normalizeCatalogTasks(nextItems)
}

function formatSessionOption(session) {
  if (!session) return ''
  const flags = []
  if (session.is_current) flags.push('live')
  else if (session.is_latest) flags.push('latest')
  const worldHealth = normalizeSessionWorldHealth(session.world_health || null)
  const runtimeFault = normalizeSessionRuntimeFault(session.runtime_fault_summary || null)
  if (runtimeFault?.degraded) flags.push(formatSessionFaultFlag(runtimeFault))
  if (worldHealth?.ended_stale) flags.push('stale')
  else if (worldHealth?.stale_seen) flags.push('sync')
  if (worldHealth?.max_consecutive_failures) {
    const syncSuffix = worldHealth.failure_threshold
      ? `${worldHealth.max_consecutive_failures}/${worldHealth.failure_threshold}`
      : `${worldHealth.max_consecutive_failures}`
    flags.push(`sync=${syncSuffix}`)
  } else if (!worldHealth?.stale_seen && worldHealth?.slow_events) {
    flags.push(`slow=${worldHealth.slow_events}`)
  }
  const rollup = normalizeSessionTaskRollup(session.task_rollup || null)
  if (rollup?.nonTerminal) flags.push(`nt=${rollup.nonTerminal}`)
  for (const [status, label] of [['failed', 'failed'], ['partial', 'partial'], ['aborted', 'aborted']]) {
    const count = rollup?.statuses?.find((item) => item.status === status)?.count || 0
    if (count) flags.push(`${label}=${count}`)
  }
  const suffix = flags.length ? ` · ${flags.join('/')}` : ''
  return `${session.session_name}${suffix}`
}

function normalizeSessionTaskRollup(raw) {
  if (!raw || typeof raw !== 'object') return null
  const total = Number(raw.total || 0)
  const nonTerminal = Number(raw.non_terminal || 0)
  const terminal = Number(raw.terminal || 0)
  const byStatus = raw.by_status && typeof raw.by_status === 'object' ? raw.by_status : {}
  const statuses = Object.entries(byStatus)
    .map(([status, count]) => ({ status, count: Number(count || 0) }))
    .filter((item) => item.count > 0)
  if (!total && !nonTerminal && !terminal && !statuses.length) return null
  return {
    total,
    nonTerminal,
    terminal,
    statuses,
  }
}

function normalizeSessionWorldHealth(raw) {
  if (!raw || typeof raw !== 'object') return null
  const normalized = {
    stale_seen: !!raw.stale_seen,
    ended_stale: !!raw.ended_stale,
    stale_refreshes: Number(raw.stale_refreshes || 0),
    max_consecutive_failures: Number(raw.max_consecutive_failures || 0),
    failure_threshold: Number(raw.failure_threshold || 0),
    slow_events: Number(raw.slow_events || 0),
    max_totalMs: Number(raw.max_total_ms || 0),
    last_failure_layer: raw.last_failure_layer || '',
    last_error: raw.last_error || '',
    last_error_detail: raw.last_error_detail || '',
  }
  if (
    !normalized.stale_seen
    && !normalized.ended_stale
    && !normalized.stale_refreshes
    && !normalized.max_consecutive_failures
    && !normalized.failure_threshold
    && !normalized.slow_events
    && !normalized.max_totalMs
    && !normalized.last_failure_layer
    && !normalized.last_error
    && !normalized.last_error_detail
  ) {
    return null
  }
  return normalized
}

function normalizeSessionRuntimeFault(raw) {
  if (!raw || typeof raw !== 'object') return null
  const degraded = !!raw.degraded
  const source = raw.source || ''
  const stage = raw.stage || ''
  const error = raw.error || ''
  const updatedAt = Number(raw.updated_at || 0)
  const hasFaultMarker = !!(degraded || source || stage || error || updatedAt)
  let count = Number(raw.count || 0)
  let firstAt = Number(raw.first_at || 0)
  if (hasFaultMarker && count <= 0) count = 1
  if (hasFaultMarker && !firstAt) firstAt = updatedAt
  const normalized = {
    degraded,
    source,
    stage,
    error,
    count,
    first_at: firstAt,
    updated_at: updatedAt,
    breakdown: Array.isArray(raw.breakdown)
      ? raw.breakdown
        .map((item) => ({
          source: String(item?.source || ''),
          stage: String(item?.stage || ''),
          count: Number(item?.count || 0),
        }))
        .filter((item) => item.count > 0 && (item.source || item.stage))
      : count > 0 && (source || stage)
        ? [{
            source,
            stage,
            count,
          }]
      : [],
  }
  if (
    !normalized.degraded
    && !normalized.source
    && !normalized.stage
    && !normalized.error
    && !normalized.count
    && !normalized.first_at
    && !normalized.updated_at
  ) {
    return null
  }
  return normalized
}

function formatSessionHealthStatus(health) {
  if (!health) return ''
  if (health.ended_stale) return 'Session 世界同步异常'
  if (health.stale_seen) return 'Session 曾出现世界同步异常'
  if (health.slow_events) return 'Session 出现慢刷新'
  return 'Session 运行摘要'
}

function formatSessionHealthError(error) {
  if (!error) return ''
  const text = String(error)
  return text.length > 48 ? `${text.slice(0, 45)}...` : text
}

function formatSessionFaultFlag(fault) {
  if (!fault?.degraded) return ''
  const when = formatSessionFaultTime(fault.updated_at)
  const countSuffix = fault.count > 1 ? `x${fault.count}` : ''
  return when ? `fault${countSuffix}@${when}` : `fault${countSuffix}`
}

function formatSessionFaultBreakdownItem(item) {
  if (!item) return ''
  const source = String(item.source || '')
  const stage = String(item.stage || '')
  const count = Number(item.count || 0)
  const key = stage ? `${source}/${stage}` : source
  return key && count > 0 ? `fault=${key}×${count}` : ''
}

function formatSessionFaultTime(ts) {
  if (!ts) return ''
  const date = new Date(Number(ts) * 1000)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.toISOString().slice(11, 19)}Z`
}

function refreshDiagnostics() {
  if (!props.send) return
  props.send('diagnostics_sync_request')
}

function replayCacheKey(taskId, sessionDir = selectedSessionDir.value || currentSessionDir.value || '') {
  return `${sessionDir || 'latest'}::${taskId || ''}`
}

function isSelectedSessionLive() {
  const selected = selectedSessionDir.value || currentSessionDir.value || ''
  const current = currentSessionDir.value || ''
  return !selected || !current || selected === current
}

function isTaskActive(taskId) {
  const task = activeTaskCatalog.value.find((item) => item.task_id === taskId)
  return task ? !TERMINAL_TASK_STATUS.has(String(task.status || '')) : true
}

function clearReplayRefreshTimers() {
  replayRefreshTimers.forEach((timerId) => clearTimeout(timerId))
  replayRefreshTimers.clear()
}

function resolveTaskId(payload = {}) {
  return payload.task_id || payload.holder_task_id || payload.data?.task_id || null
}

function resolveJobId(payload = {}) {
  return payload.job_id || payload.holder_job_id || payload.data?.job_id || null
}

function addTraceEntry(entry) {
  traceEntries.value.push(entry)
  if (traceEntries.value.length > 800) traceEntries.value.splice(0, 200)
}

function mergeTraceEntries(left, right) {
  const merged = []
  const seen = new Set()
  for (const entry of [...left, ...right]) {
    const key = [
      entry.timestamp || 0,
      entry.source || '',
      entry.taskId || '',
      entry.jobId || '',
      entry.message || '',
    ].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(entry)
  }
  return merged.sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0))
}

function traceEntryFromLogRecord(record, fallbackTaskId = null, replayed = false) {
  const taskId = resolveTaskId(record.data || {}) || fallbackTaskId
  const jobId = resolveJobId(record.data || {})
  if (taskId) registerTaskLabel(taskId)
  const message = replaceTaskIdsWithLabels(record.message || JSON.stringify(record))
  return {
    timestamp: record.timestamp || Date.now() / 1000,
    source: record.component || 'log',
    taskId,
    taskLabel: taskId ? formatTaskLabel(taskId) : null,
    jobId,
    message: `${replayed ? '[replay] ' : ''}[${record.event || record.level || 'log'}] ${message}`,
    details: record.data || null,
  }
}

function traceEntryCompactKey(entry) {
  const details = entry?.details ? formatTraceDetails(entry.details) : ''
  return [
    entry?.source || '',
    entry?.taskId || '',
    entry?.jobId || '',
    entry?.message || '',
    details,
  ].join('|')
}

function compactTraceEntries(entries) {
  const compacted = []
  for (const entry of entries || []) {
    const key = traceEntryCompactKey(entry)
    const previous = compacted[compacted.length - 1]
    if (previous && previous._compactKey === key) {
      previous.repeatCount = Number(previous.repeatCount || 1) + 1
      previous.timestamp = entry.timestamp
      continue
    }
    compacted.push({
      ...entry,
      repeatCount: 1,
      _compactKey: key,
    })
  }
  return compacted.map(({ _compactKey, ...entry }) => entry)
}

function formatTraceDetails(details) {
  try {
    return JSON.stringify(details, null, 2)
  } catch {
    return String(details)
  }
}

function formatReplayItem(item) {
  if (!item) return ''
  const label = item.label ? `[${item.label}] ` : ''
  const data = item.data && typeof item.data === 'object' ? item.data : {}
  if (['unit_request_fulfilled', 'unit_request_start_released', 'unit_request_cancelled'].includes(item.label)) {
    const extra = []
    if (data.request_id) extra.push(`req=${compactSingleLine(data.request_id, 24)}`)
    if (data.reservation_id) extra.push(`res=${compactSingleLine(data.reservation_id, 24)}`)
    if (data.status) extra.push(`status=${compactSingleLine(data.status, 16)}`)
    if (Number(data.assigned_count || 0)) extra.push(`assigned=${Number(data.assigned_count)}`)
    if (Number(data.produced_count || 0)) extra.push(`produced=${Number(data.produced_count)}`)
    if (Number(data.remaining_count || 0)) extra.push(`remaining=${Number(data.remaining_count)}`)
    const suffix = extra.length ? ` · ${extra.join(' · ')}` : ''
    return `${label}${item.message || ''}${suffix}`
  }
  return `${label}${item.message || ''}`
}

function formatActorPreview(actorIds) {
  if (!Array.isArray(actorIds) || !actorIds.length) return ''
  const preview = actorIds.slice(0, 4).join(',')
  return actorIds.length > 4 ? `${preview}…` : preview
}

function compactSingleLine(text, maxLength = 64) {
  const compact = String(text || '').replace(/\s+/g, ' ').trim()
  if (!compact) return ''
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 3)}...` : compact
}

function taskOptionDetail(task) {
  const triageStatus = compactSingleLine(task?.triage?.status_line || '', 48)
  if (triageStatus) return triageStatus
  const summary = compactSingleLine(task?.summary || '', 48)
  if (summary) return summary
  const expert = compactSingleLine(task?.triage?.active_expert || '', 24)
  if (expert) return `expert=${expert}`
  const status = compactSingleLine(task?.status || '', 24)
  return status ? `status=${status}` : ''
}

function formatTaskOption(task) {
  const label = compactSingleLine(task?.label || formatTaskLabel(task?.task_id), 16) || '任务'
  const rawText = compactSingleLine(task?.raw_text || '未命名任务', 24)
  const detail = taskOptionDetail(task)
  return detail ? `${label} · ${rawText} · ${detail}` : `${label} · ${rawText}`
}

function toggleRawReplay(taskId) {
  if (!taskId || taskId === 'ALL') return
  const key = replayCacheKey(taskId)
  replayExpanded[key] = !replayExpanded[key]
  if (replayExpanded[key] && !replayMetaCache[key]?.raw_entries_included) {
    requestReplay(taskId, { force: true, includeEntries: true })
  }
}

function requestReplay(taskId, { force = false, includeEntries = false } = {}) {
  if (!props.send || !taskId || taskId === 'ALL') return
  const key = replayCacheKey(taskId)
  const requestedLevel = Number(replayRequestedLevel[key] || 0)
  const neededLevel = includeEntries ? 2 : 1
  if (!force && requestedLevel >= neededLevel) return
  if (force) delete replayRequestedLevel[key]
  const sent = props.send('task_replay_request', {
    task_id: taskId,
    session_dir: selectedSessionDir.value || currentSessionDir.value || null,
    include_entries: includeEntries,
  })
  if (sent) replayRequestedLevel[key] = neededLevel
}

function focusDiagnosticsTask(taskId) {
  if (!taskId || taskId === 'ALL') return
  selectedTaskId.value = taskId
  requestReplay(taskId, { force: true, includeEntries: Boolean(replayExpanded[replayCacheKey(taskId)]) })
}

function jumpToDiagnosticsTask(taskId) {
  if (!taskId || taskId === 'ALL') return
  window.dispatchEvent(
    new CustomEvent('theseed:focus-diagnostics-task', {
      detail: { taskId },
    }),
  )
}

function scheduleReplayRefresh(taskId) {
  if (!taskId || taskId === 'ALL') return
  if (taskId !== selectedTaskId.value) return
  if (!isSelectedSessionLive()) return
  if (!isTaskActive(taskId)) return
  const key = replayCacheKey(taskId)
  const timerId = replayRefreshTimers.get(key)
  if (timerId) clearTimeout(timerId)
  replayRefreshTimers.set(
    key,
    window.setTimeout(() => {
      replayRefreshTimers.delete(key)
      requestReplay(taskId, { force: true, includeEntries: Boolean(replayExpanded[key]) })
    }, REPLAY_REFRESH_DEBOUNCE_MS),
  )
}

function prefetchRecentReplays(tasks) {
  const candidates = []
  if (selectedTaskId.value && selectedTaskId.value !== 'ALL') {
    candidates.push(selectedTaskId.value)
  }
  const ordered = tasks || []
  const firstTask = ordered[0]?.task_id
  if (firstTask) candidates.push(firstTask)
  const firstActiveTask = ordered.find((task) => !['succeeded', 'failed', 'aborted', 'partial'].includes(task?.status))?.task_id
  if (firstActiveTask) candidates.push(firstActiveTask)

  for (const taskId of [...new Set(candidates)]) {
    requestReplay(taskId, { includeEntries: false })
  }
}

function clearDiagnostics() {
  logEntries.value = []
  traceEntries.value = []
  operatorMessages.value = []
  liveTaskCatalog.value = []
  sessionCatalog.value = []
  sessionTaskCatalog.value = []
  liveRuntimeState.value = {}
  liveUnitPipelinePreview.value = ''
  liveUnitPipelineFocus.value = {
    detail: '',
    taskId: '',
    taskLabel: '',
    requestCount: 0,
    reservationCount: 0,
  }
  selectedSessionDir.value = ''
  selectedTaskId.value = 'ALL'
  filterLevel.value = 'ALL'
  filterComponent.value = 'ALL'
  Object.keys(benchmarkStats).forEach((key) => delete benchmarkStats[key])
  Object.keys(replayCache).forEach((key) => delete replayCache[key])
  Object.keys(replayBundleCache).forEach((key) => delete replayBundleCache[key])
  Object.keys(replayMetaCache).forEach((key) => delete replayMetaCache[key])
  Object.keys(replayRequestedLevel).forEach((key) => delete replayRequestedLevel[key])
  Object.keys(replayExpanded).forEach((key) => delete replayExpanded[key])
  clearReplayRefreshTimers()
  lastWorldTruthSignature = ''
}

function addLog(entry) {
  logEntries.value.push(entry)
  if (logEntries.value.length > 500) logEntries.value.splice(0, 100)
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

function normalizeLogEntry(entry) {
  const message = replaceTaskIdsWithLabels(entry?.message || JSON.stringify(entry))
  return {
    component: entry?.component || entry?.tag || 'log',
    level: entry?.level || 'INFO',
    tag: entry?.event || entry?.tag || entry?.component || 'log',
    message,
    timestamp: entry?.timestamp || Date.now() / 1000,
  }
}

function replaceSessionHistory(payload = {}) {
  const rawLogEntries = Array.isArray(payload.log_entries) ? payload.log_entries : []
  logEntries.value = rawLogEntries.map((entry) => normalizeLogEntry(entry)).slice(-500)
  const structuredOperatorEntries = structuredOperatorMessagesFromSessionHistory(payload)
  const rawOperatorEntries = Array.isArray(payload.player_visible_entries) ? payload.player_visible_entries : []
  operatorMessages.value = (structuredOperatorEntries.length
    ? structuredOperatorEntries
    : rawOperatorEntries.length
      ? rawOperatorEntries.map((entry) => operatorMessageFromHistoryEntry(entry))
      : rawLogEntries.map((entry) => operatorMessageFromLogRecord(entry))
  )
    .filter(Boolean)
    .slice(-40)
  traceEntries.value = rawLogEntries
    .map((entry) => traceEntryFromLogRecord(entry))
    .filter((entry) => entry.taskId || entry.jobId)
    .slice(-800)
  replaceBenchmarkSnapshot(Array.isArray(payload.benchmark_records) ? payload.benchmark_records : [])
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

function appendBenchmarkRecords(records) {
  if (!Array.isArray(records)) return
  for (const record of records) {
    if (!record?.tag) continue
    const current = benchmarkStats[record.tag] || { count: 0, avg: 0, max: 0, total: 0 }
    const duration = Number(record.duration_ms || 0)
    const count = current.count + 1
    const total = (current.total || current.avg * current.count || 0) + duration
    benchmarkStats[record.tag] = {
      count,
      total,
      avg: total / count,
      max: Math.max(current.max || 0, duration),
    }
  }
}

function replaceBenchmarkSnapshot(records) {
  Object.keys(benchmarkStats).forEach((key) => delete benchmarkStats[key])
  appendBenchmarkRecords(records)
}

function normalizeOperatorMessage({ timestamp, label, taskId = '', message = '', detail = null }) {
  const resolvedTaskId = String(taskId || '')
  if (resolvedTaskId) registerTaskLabel(resolvedTaskId)
  return {
    key: [
      String(timestamp || 0),
      label,
      resolvedTaskId,
      String(message || ''),
    ].join('|'),
    timestamp: Number(timestamp || 0),
    label: String(label || 'operator'),
    taskId: resolvedTaskId,
    taskLabel: resolvedTaskId ? formatTaskLabel(resolvedTaskId) : '',
    message: replaceTaskIdsWithLabels(String(message || '')),
    detail,
  }
}

function operatorMessageFromQueryHistoryEntry(entry) {
  if (!entry || typeof entry !== 'object') return null
  return normalizeOperatorMessage({
    timestamp: entry.timestamp,
    label: 'adjutant',
    taskId: entry.task_id || '',
    message: entry.answer || '',
    detail: entry,
  })
}

function operatorMessageFromNotificationHistoryEntry(entry) {
  if (!entry || typeof entry !== 'object') return null
  return normalizeOperatorMessage({
    timestamp: entry.timestamp,
    label: 'notify',
    taskId: entry.task_id || '',
    message: entry.content || '',
    detail: entry,
  })
}

function operatorMessageFromTaskMessageHistoryEntry(entry) {
  if (!entry || typeof entry !== 'object') return null
  const messageType = String(entry.message_type || '')
  if (!['task_info', 'task_warning'].includes(messageType)) return null
  return normalizeOperatorMessage({
    timestamp: entry.timestamp,
    label: messageType === 'task_warning' ? 'task_warning' : 'task_info',
    taskId: entry.task_id || '',
    message: entry.content || '',
    detail: entry,
  })
}

function structuredOperatorMessagesFromSessionHistory(payload = {}) {
  const queryEntries = Array.isArray(payload.query_response_entries) ? payload.query_response_entries : []
  const notificationEntries = Array.isArray(payload.notification_entries) ? payload.notification_entries : []
  const taskMessageEntries = Array.isArray(payload.task_message_entries) ? payload.task_message_entries : []
  const messages = [
    ...queryEntries.map((entry) => operatorMessageFromQueryHistoryEntry(entry)),
    ...notificationEntries.map((entry) => operatorMessageFromNotificationHistoryEntry(entry)),
    ...taskMessageEntries.map((entry) => operatorMessageFromTaskMessageHistoryEntry(entry)),
  ]
    .filter(Boolean)
    .sort((a, b) => Number(a?.timestamp || 0) - Number(b?.timestamp || 0))
  return messages.slice(-40)
}

function addOperatorMessage(item) {
  if (!item) return
  operatorMessages.value.push(item)
  if (operatorMessages.value.length > 40) operatorMessages.value.splice(0, operatorMessages.value.length - 40)
}

function operatorMessageFromLogRecord(record) {
  const event = String(record?.event || '')
  const data = record?.data && typeof record.data === 'object' ? record.data : {}
  if (event === 'adjutant_response_sent') {
    return normalizeOperatorMessage({
      timestamp: record?.timestamp,
      label: 'adjutant',
      taskId: resolveTaskId(data) || '',
      message: data.content || record?.message || '收到副官回复',
      detail: data,
    })
  }
  if (event === 'player_notification_sent') {
    const nested = data?.data && typeof data.data === 'object' ? data.data : {}
    return normalizeOperatorMessage({
      timestamp: record?.timestamp,
      label: 'notify',
      taskId: resolveTaskId(nested) || resolveTaskId(data) || '',
      message: data.content || record?.message || '收到通知',
      detail: data,
    })
  }
  if (event === 'task_message_registered' && ['task_info', 'task_warning'].includes(String(data?.message_type || ''))) {
    return normalizeOperatorMessage({
      timestamp: record?.timestamp,
      label: String(data?.message_type || '') === 'task_warning' ? 'task_warning' : 'task_info',
      taskId: resolveTaskId(data) || '',
      message: data.content || record?.message || '收到任务消息',
      detail: data,
    })
  }
  if (['task_info', 'task_warning'].includes(event)) {
    return normalizeOperatorMessage({
      timestamp: record?.timestamp,
      label: event,
      taskId: resolveTaskId(data) || '',
      message: data.content || record?.message || '收到任务消息',
      detail: data,
    })
  }
  return null
}

function operatorMessageFromHistoryEntry(entry) {
  if (!entry || typeof entry !== 'object') return null
  const kind = String(entry.kind || '')
  let label = 'operator'
  if (kind === 'adjutant') label = 'adjutant'
  else if (kind === 'notification') label = 'notify'
  else if (kind === 'task_message') {
    label = String(entry.message_type || '') === 'task_warning' ? 'task_warning' : 'task_info'
  }
  return normalizeOperatorMessage({
    timestamp: entry.timestamp,
    label,
    taskId: entry.task_id || '',
    message: entry.content || '',
    detail: entry,
  })
}

function formatJsonBlock(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const offHandlers = []
let clearUiHandler = null
let focusTaskHandler = null

if (props.on) {
  offHandlers.push(props.on('log_entry', (msg) => {
    if (!isSelectedSessionLive()) return
    const entry = msg.data || msg
    addLog(normalizeLogEntry(entry))
    const traceEntry = traceEntryFromLogRecord(entry)
    if (traceEntry.taskId || traceEntry.jobId) {
      addTraceEntry(traceEntry)
      if (traceEntry.taskId) scheduleReplayRefresh(traceEntry.taskId)
    }
  }))
  offHandlers.push(props.on('world_snapshot', (msg) => {
    const nextWorldSyncStale = !!msg.data?.stale
    const nextWorldSyncFailures = Number(msg.data?.consecutive_refresh_failures || 0)
    const nextWorldSyncFailureThreshold = Number(msg.data?.failure_threshold || 0)
    const nextWorldSyncError = String(msg.data?.last_refresh_error || '')
    const nextCapabilityTruthBlocker = String(msg.data?.capability_truth_blocker || '')
    const nextCapabilityTruthFaction = String(msg.data?.player_faction || '')
    const nextRuntimeFault = msg.data?.runtime_fault_state || {}
    const nextUnitPipelineFocus = msg.data?.unit_pipeline_focus || {}
    liveRuntimeState.value = msg.data?.runtime_state && typeof msg.data.runtime_state === 'object'
      ? msg.data.runtime_state
      : {}
    liveUnitPipelinePreview.value = String(msg.data?.unit_pipeline_preview || '')
    liveUnitPipelineFocus.value = {
      detail: String(nextUnitPipelineFocus.detail || ''),
      taskId: String(nextUnitPipelineFocus.task_id || ''),
      taskLabel: String(nextUnitPipelineFocus.task_label || ''),
      requestCount: Number(nextUnitPipelineFocus.request_count || 0),
      reservationCount: Number(nextUnitPipelineFocus.reservation_count || 0),
    }

    worldSyncStale.value = nextWorldSyncStale
    worldSyncFailures.value = nextWorldSyncFailures
    worldSyncFailureThreshold.value = nextWorldSyncFailureThreshold
    worldSyncError.value = nextWorldSyncError
    capabilityTruthBlocker.value = nextCapabilityTruthBlocker
    capabilityTruthFaction.value = nextCapabilityTruthFaction
    runtimeFaultState.value = normalizeSessionRuntimeFault(nextRuntimeFault) || {
      degraded: false,
      source: '',
      stage: '',
      error: '',
      count: 0,
      first_at: 0,
      updated_at: 0,
    }

    const nextWorldTruthSignature = [
      nextWorldSyncStale,
      nextWorldSyncFailures,
      nextWorldSyncFailureThreshold,
      nextWorldSyncError,
      nextCapabilityTruthBlocker,
      nextCapabilityTruthFaction,
      runtimeFaultState.value.degraded,
      runtimeFaultState.value.source,
      runtimeFaultState.value.stage,
      runtimeFaultState.value.error,
      runtimeFaultState.value.count,
      runtimeFaultState.value.first_at,
      runtimeFaultState.value.updated_at,
    ].join('|')
    if (nextWorldTruthSignature !== lastWorldTruthSignature) {
      lastWorldTruthSignature = nextWorldTruthSignature
      scheduleReplayRefresh(selectedTaskId.value)
    }
    if (isSelectedSessionLive() && msg.data?.benchmark) replaceBenchmarkSnapshot(msg.data.benchmark)
  }))
  offHandlers.push(props.on('benchmark', (msg) => {
    if (!isSelectedSessionLive()) return
    if (!msg.data?.records) return
    if (msg.data?.replace) replaceBenchmarkSnapshot(msg.data.records)
    else appendBenchmarkRecords(msg.data.records)
  }))
  offHandlers.push(props.on('task_list', (msg) => {
    const tasks = msg.data?.tasks || []
    setLiveTaskCatalog(tasks)
    prefetchRecentReplays(activeTaskCatalog.value)
  }))
  offHandlers.push(props.on('task_update', (msg) => {
    const task = msg.data || {}
    if (!task.task_id) return
    mergeLiveTask(task)
    registerTaskLabel(task.task_id)
    if (!isSelectedSessionLive()) return
    addTraceEntry({
      timestamp: task.timestamp || msg.timestamp,
      source: 'task',
      taskId: task.task_id,
      taskLabel: formatTaskLabel(task.task_id),
      jobId: null,
      message: `状态更新：${task.status}${task.raw_text ? ` · ${task.raw_text}` : ''}`,
      details: task,
    })
    const replayKey = replayCacheKey(task.task_id)
    delete replayRequestedLevel[replayKey]
    delete replayCache[replayKey]
    delete replayBundleCache[replayKey]
    delete replayMetaCache[replayKey]
    if (task.task_id === selectedTaskId.value) {
      requestReplay(task.task_id, { force: true, includeEntries: Boolean(replayExpanded[replayKey]) })
    }
  }))
  offHandlers.push(props.on('query_response', (msg) => {
    if (!isSelectedSessionLive()) return
    const taskId = msg.data?.task_id || null
    addOperatorMessage(normalizeOperatorMessage({
      timestamp: msg.timestamp,
      label: 'adjutant',
      taskId,
      message: msg.data?.answer || msg.data?.response_text || '收到副官回复',
      detail: msg.data || null,
    }))
    if (!taskId) return
    addTraceEntry({
      timestamp: msg.timestamp,
      source: 'adjutant',
      taskId,
      taskLabel: formatTaskLabel(taskId),
      jobId: msg.data?.job_id || null,
      message: replaceTaskIdsWithLabels(msg.data?.answer || msg.data?.response_text || '收到副官回复'),
      details: msg.data || null,
    })
    scheduleReplayRefresh(taskId)
  }))
  offHandlers.push(props.on('player_notification', (msg) => {
    if (!isSelectedSessionLive()) return
    const taskId = msg.data?.task_id || msg.data?.data?.task_id || null
    addOperatorMessage(normalizeOperatorMessage({
      timestamp: msg.timestamp,
      label: 'notify',
      taskId,
      message: msg.data?.content || JSON.stringify(msg.data),
      detail: msg.data || null,
    }))
    if (!taskId) return
    addTraceEntry({
      timestamp: msg.timestamp,
      source: 'notify',
      taskId,
      taskLabel: formatTaskLabel(taskId),
      jobId: null,
      message: replaceTaskIdsWithLabels(msg.data?.content || JSON.stringify(msg.data)),
      details: msg.data || null,
    })
    scheduleReplayRefresh(taskId)
  }))
  offHandlers.push(props.on('task_message', (msg) => {
    if (!isSelectedSessionLive()) return
    const payload = msg.data || {}
    const taskId = payload.task_id || null
    addOperatorMessage(normalizeOperatorMessage({
      timestamp: msg.timestamp,
      label: payload.type === 'task_warning' ? 'task_warning' : 'task_info',
      taskId,
      message: payload.content || JSON.stringify(payload),
      detail: payload,
    }))
    if (!taskId) return
    registerTaskLabel(taskId)
    addTraceEntry({
      timestamp: msg.timestamp,
      source: 'task_message',
      taskId,
      taskLabel: formatTaskLabel(taskId),
      jobId: null,
      message: replaceTaskIdsWithLabels(payload.content || JSON.stringify(payload)),
      details: payload,
    })
    scheduleReplayRefresh(taskId)
  }))
  offHandlers.push(props.on('task_replay', (msg) => {
    const payload = msg.data || {}
    const taskId = payload.task_id
    if (!taskId) return
    const replayKey = replayCacheKey(taskId, payload.session_dir || '')
    replayBundleCache[replayKey] = payload.bundle || null
    replayMetaCache[replayKey] = {
      raw_entry_count: Number(payload.raw_entry_count || 0),
      entry_count: Number(payload.entry_count || 0),
      raw_entries_truncated: Boolean(payload.raw_entries_truncated),
      raw_entries_included: Boolean(payload.raw_entries_included),
    }
    replayCache[replayKey] = Array.isArray(payload.entries)
      ? payload.entries.map((entry) => traceEntryFromLogRecord(entry, taskId, true))
      : []
    replayRequestedLevel[replayKey] = Boolean(payload.raw_entries_included) ? 2 : 1
  }))
  offHandlers.push(props.on('session_catalog', (msg) => {
    const payload = msg.data || {}
    sessionCatalog.value = Array.isArray(payload.sessions) ? payload.sessions : []
    const selected = payload.selected_session_dir || currentSessionDir.value || ''
    if (!selectedSessionDir.value || !sessionCatalog.value.some((item) => item.session_dir === selectedSessionDir.value)) {
      selectedSessionDir.value = selected
    }
  }))
  offHandlers.push(props.on('session_task_catalog', (msg) => {
    const payload = msg.data || {}
    sessionTaskCatalog.value = normalizeCatalogTasks(payload.tasks || [])
    const exists = activeTaskCatalog.value.some((task) => task.task_id === selectedTaskId.value)
    if (!exists) selectedTaskId.value = 'ALL'
    prefetchRecentReplays(activeTaskCatalog.value)
  }))
  offHandlers.push(props.on('session_history', (msg) => {
    const payload = msg.data || {}
    const targetSession = String(payload.session_dir || '')
    const selected = selectedSessionDir.value || currentSessionDir.value || ''
    if (selected && targetSession && selected !== targetSession) return
    replaceSessionHistory(payload)
  }))
  offHandlers.push(props.on('session_cleared', () => {
    clearDiagnostics()
  }))
}

onMounted(() => {
  if (props.send) {
    props.send('diagnostics_sync_request')
  }
  clearUiHandler = () => clearDiagnostics()
  focusTaskHandler = (event) => {
    const taskId = event?.detail?.taskId
    if (!taskId) return
    const liveSession = currentSessionDir.value || ''
    if (liveSession) selectedSessionDir.value = liveSession
    focusDiagnosticsTask(taskId)
  }
  window.addEventListener('theseed:clear-ui', clearUiHandler)
  window.addEventListener('theseed:apply-diagnostics-focus', focusTaskHandler)
})

onUnmounted(() => {
  offHandlers.forEach((off) => {
    if (typeof off === 'function') off()
  })
  clearReplayRefreshTimers()
  if (clearUiHandler) window.removeEventListener('theseed:clear-ui', clearUiHandler)
  if (focusTaskHandler) window.removeEventListener('theseed:apply-diagnostics-focus', focusTaskHandler)
})

watch(selectedTaskId, (taskId) => {
  requestReplay(taskId, { includeEntries: false })
})

watch(selectedSessionDir, (sessionDir) => {
  if (!props.send || !sessionDir) return
  props.send('session_select', { session_dir: sessionDir })
})
</script>

<style scoped>
.diag-panel { padding: 12px; display: flex; flex-direction: column; height: 100%; }
.diag-panel h3 { margin: 8px 0; font-size: 14px; color: #666; }
.trace-controls {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}
.trace-label {
  font-size: 12px;
  color: #666;
}
.trace-session-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.trace-select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
}
.trace-stream {
  flex: 1;
  min-height: 250px;
  max-height: 40vh;
  overflow-y: auto;
  margin-bottom: 10px;
  padding: 8px;
  border: 1px solid #eceff1;
  border-radius: 6px;
  background: #fafbfc;
  font-family: monospace;
  font-size: 12px;
}
.task-log-path {
  font-size: 10px;
  color: #607d8b;
  font-family: monospace;
  word-break: break-all;
  padding: 2px 4px;
  background: #f5f5f5;
  border-radius: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.selected-task-meta {
  margin-top: 2px;
}
.triage-summary {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #eef6ff;
  border: 1px solid #d4e7fb;
}
.replay-summary {
  margin-bottom: 10px;
  padding: 10px;
  border-radius: 6px;
  background: #fff9eb;
  border: 1px solid #f2ddb0;
}
.replay-title {
  font-size: 12px;
  font-weight: 700;
  color: #7a5200;
  margin-bottom: 4px;
}
.replay-overview {
  font-size: 12px;
  color: #5c4200;
  margin-bottom: 6px;
}
.replay-section {
  margin-top: 8px;
}
.replay-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.replay-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f0f4f8;
  color: #4c6170;
  font-size: 11px;
  font-family: monospace;
}
.replay-toggle {
  margin-top: 8px;
}
.replay-heading {
  font-size: 11px;
  font-weight: 600;
  color: #8b6b13;
  margin-bottom: 4px;
}
.replay-detail {
  margin-top: 8px;
  border: 1px solid #ead7a1;
  border-radius: 6px;
  background: #fffdf5;
  padding: 6px 8px;
}
.replay-detail summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  color: #7a5200;
}
.replay-detail-card {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #ead7a1;
}
.replay-item {
  font-size: 11px;
  color: #5d4a18;
  margin-bottom: 3px;
  word-break: break-word;
}
.replay-blocker {
  color: #8a3c16;
}
.triage-status {
  font-size: 12px;
  color: #1f3c5b;
  font-weight: 600;
  margin-bottom: 4px;
}
.triage-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: #55708c;
  font-family: monospace;
}
.trace-entry {
  margin-bottom: 6px;
}
.trace-details {
  margin: 4px 0 0 58px;
  padding: 6px 8px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: #ffffff;
  border: 1px solid #e0e6eb;
  border-radius: 6px;
  color: #37474f;
}
.trace-source { color: #455a64; margin-right: 6px; }
.trace-task {
  color: #1565c0;
  margin-right: 6px;
}
.trace-job {
  color: #6a1b9a;
  margin-right: 6px;
}
.trace-msg {
  color: #222;
  white-space: pre-wrap;
}
.trace-repeat {
  margin-left: 6px;
  color: #8a6d1f;
  font-weight: 600;
}
.log-filter { display: flex; gap: 4px; margin-bottom: 6px; flex-wrap: wrap; }
.filter-btn { padding: 2px 8px; border: 1px solid #ccc; border-radius: 3px; background: #f5f5f5; cursor: pointer; font-size: 11px; }
.filter-btn.active { background: #1976d2; color: white; border-color: #1976d2; }
.log-stream { flex: 1; overflow-y: auto; font-family: monospace; font-size: 12px; background: #1e1e1e; color: #d4d4d4; padding: 8px; border-radius: 4px; min-height: 150px; }
.log-entry { margin-bottom: 2px; }
.log-entry.error { color: #f44336; }
.log-entry.warn, .log-entry.warning { color: #ff9800; }
.log-time { color: #888; margin-right: 6px; }
.log-component { color: #81c784; margin-right: 6px; }
.log-tag { color: #4fc3f7; margin-right: 6px; }
.empty { color: #999; font-size: 13px; }
.benchmark-summary { font-size: 12px; }
.bench-row { display: flex; gap: 12px; padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
.bench-tag { font-weight: bold; min-width: 100px; }
.bench-count { color: #666; }
.bench-avg { color: #2196f3; }
.bench-max { color: #f44336; }
.bench-note { margin-top: 6px; font-size: 11px; color: #999; }
</style>

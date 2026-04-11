<template>
  <div class="diag-panel">
    <h3>Diagnostics</h3>
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
      <label class="trace-label" for="task-trace-select">Task Trace</label>
      <select id="task-trace-select" v-model="selectedTaskId" class="trace-select">
        <option value="ALL">全部任务</option>
        <option v-for="task in activeTaskCatalog" :key="task.task_id" :value="task.task_id">
          {{ task.label }} · {{ task.raw_text || '未命名任务' }}
        </option>
      </select>
      <div v-if="selectedTaskLogPath" class="task-log-path" :title="selectedTaskLogPath">
        📄 {{ selectedTaskLogPath }}
      </div>
    </div>
    <div v-if="selectedTaskTriage" class="triage-summary">
      <div class="triage-status">{{ selectedTaskTriage.status_line }}</div>
      <div class="triage-meta">
        <span>state={{ selectedTaskTriage.state }}</span>
        <span v-if="selectedTaskTriage.phase">phase={{ selectedTaskTriage.phase }}</span>
        <span v-if="selectedTaskTriage.active_expert">expert={{ selectedTaskTriage.active_expert }}</span>
        <span v-if="selectedTaskTriage.active_group_size">group={{ selectedTaskTriage.active_group_size }}</span>
        <span v-if="selectedTaskTriage.world_stale">world=stale</span>
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
      <div v-if="selectedTaskReplayBundle.current_runtime?.triage" class="replay-section">
        <div class="replay-heading">Current Runtime</div>
        <div class="replay-overview">{{ selectedTaskReplayBundle.current_runtime.triage.status_line }}</div>
        <div class="triage-meta">
          <span>state={{ selectedTaskReplayBundle.current_runtime.triage.state }}</span>
          <span v-if="selectedTaskReplayBundle.current_runtime.triage.phase">
            phase={{ selectedTaskReplayBundle.current_runtime.triage.phase }}
          </span>
          <span v-if="selectedTaskReplayBundle.current_runtime.triage.active_expert">
            expert={{ selectedTaskReplayBundle.current_runtime.triage.active_expert }}
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
import { ref, computed, nextTick, reactive, defineProps, onMounted, onUnmounted, watch } from 'vue'
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
const filterLevel = ref('ALL')
const filterComponent = ref('ALL')
const selectedTaskId = ref('ALL')
const liveTaskCatalog = ref([])
const sessionCatalog = ref([])
const sessionTaskCatalog = ref([])
const selectedSessionDir = ref('')
const traceEntries = ref([])
const replayCache = reactive({})
const replayBundleCache = reactive({})
const replayMetaCache = reactive({})
const replayRequestedLevel = reactive({})
const replayExpanded = reactive({})
const replayRefreshTimers = new Map()

const currentSessionDir = computed(() =>
  sessionCatalog.value.find((item) => item.is_current)?.session_dir
  || sessionCatalog.value.find((item) => item.is_latest)?.session_dir
  || ''
)

const selectedSessionMeta = computed(() =>
  sessionCatalog.value.find((item) => item.session_dir === selectedSessionDir.value) || null
)

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
    return items.slice(-EXPANDED_TRACE_LIMIT)
  }
  return items.slice(-200)
})

const selectedTaskLogPath = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  const task = activeTaskCatalog.value.find(t => t.task_id === selectedTaskId.value)
  return task?.log_path || null
})

const selectedTaskTriage = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  const task = activeTaskCatalog.value.find(t => t.task_id === selectedTaskId.value)
  return task?.triage || null
})

const selectedTaskReplayBundle = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  return replayBundleCache[replayCacheKey(selectedTaskId.value)] || null
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
  const suffix = flags.length ? ` · ${flags.join('/')}` : ''
  return `${session.session_name}${suffix}`
}

function refreshDiagnostics() {
  if (!props.send) return
  props.send('sync_request')
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
  return `${label}${item.message || ''}`
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
  liveTaskCatalog.value = []
  sessionCatalog.value = []
  sessionTaskCatalog.value = []
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
}

function addLog(entry) {
  logEntries.value.push(entry)
  if (logEntries.value.length > 500) logEntries.value.splice(0, 100)
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

function formatJsonBlock(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const offHandlers = []
let clearUiHandler = null

if (props.on) {
  offHandlers.push(props.on('log_entry', (msg) => {
    const entry = msg.data || msg
    const message = replaceTaskIdsWithLabels(entry.message || JSON.stringify(entry))
    addLog({
      component: entry.component || entry.tag || 'log',
      level: entry.level || 'INFO',
      tag: entry.event || entry.tag || entry.component || 'log',
      message,
      timestamp: entry.timestamp || msg.timestamp,
    })
    const traceEntry = traceEntryFromLogRecord(entry)
    if (traceEntry.taskId || traceEntry.jobId) {
      addTraceEntry(traceEntry)
      if (traceEntry.taskId) scheduleReplayRefresh(traceEntry.taskId)
    }
  }))
  offHandlers.push(props.on('world_snapshot', (msg) => {
    if (msg.data?.benchmark) replaceBenchmarkSnapshot(msg.data.benchmark)
  }))
  offHandlers.push(props.on('benchmark', (msg) => {
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
    const taskId = msg.data?.task_id || null
    if (!taskId) return
    registerTaskLabel(taskId)
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
    const taskId = msg.data?.task_id || msg.data?.data?.task_id || null
    if (!taskId) return
    registerTaskLabel(taskId)
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
    const payload = msg.data || {}
    const taskId = payload.task_id || null
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
  offHandlers.push(props.on('session_cleared', () => {
    clearDiagnostics()
  }))
}

onMounted(() => {
  if (props.send) {
    props.send('sync_request')
  }
  clearUiHandler = () => clearDiagnostics()
  window.addEventListener('theseed:clear-ui', clearUiHandler)
})

onUnmounted(() => {
  offHandlers.forEach((off) => {
    if (typeof off === 'function') off()
  })
  clearReplayRefreshTimers()
  if (clearUiHandler) window.removeEventListener('theseed:clear-ui', clearUiHandler)
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

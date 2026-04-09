<template>
  <div class="diag-panel">
    <h3>Diagnostics</h3>
    <div class="trace-controls">
      <label class="trace-label" for="task-trace-select">Task Trace</label>
      <select id="task-trace-select" v-model="selectedTaskId" class="trace-select">
        <option value="ALL">全部任务</option>
        <option v-for="task in knownTasks" :key="task.task_id" :value="task.task_id">
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
import { ref, computed, nextTick, reactive, defineProps, onMounted, watch } from 'vue'
import {
  formatTaskLabel,
  registerTaskLabel,
  registerTaskLabels,
  replaceTaskIdsWithLabels,
} from '../composables/taskLabels.js'

const props = defineProps({ on: Function, send: Function })

const BENCHMARK_LIMIT = 20
const COMPONENT_FILTERS = ['ALL', 'adjutant', 'task_agent', 'kernel', 'expert', 'world_model', 'game_loop']
const LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARN: 2, WARNING: 2, ERROR: 3 }

const logEntries = ref([])
const logEl = ref(null)
const benchmarkStats = reactive({})
const filterLevel = ref('ALL')
const filterComponent = ref('ALL')
const selectedTaskId = ref('ALL')
const knownTasks = ref([])
const traceEntries = ref([])
const replayCache = reactive({})
const replayBundleCache = reactive({})
const replayRequested = reactive({})

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
  const items = selectedTaskId.value === 'ALL'
    ? traceEntries.value
    : mergeTraceEntries(
        replayCache[selectedTaskId.value] || [],
        traceEntries.value.filter((entry) => entry.taskId === selectedTaskId.value),
      )
  return items.slice(-200)
})

const selectedTaskLogPath = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  const task = knownTasks.value.find(t => t.task_id === selectedTaskId.value)
  return task?.log_path || null
})

const selectedTaskTriage = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  const task = knownTasks.value.find(t => t.task_id === selectedTaskId.value)
  return task?.triage || null
})

const selectedTaskReplayBundle = computed(() => {
  if (selectedTaskId.value === 'ALL') return null
  return replayBundleCache[selectedTaskId.value] || null
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

function normalizeTaskCatalog(tasks) {
  registerTaskLabels(tasks)
  knownTasks.value = [...(tasks || [])]
    .sort((a, b) => Number(b?.timestamp || b?.created_at || 0) - Number(a?.timestamp || a?.created_at || 0))
    .map((task) => ({
      ...task,
      label: formatTaskLabel(task.task_id),
      log_path: task.log_path || null,
    }))
}

function mergeKnownTask(task) {
  if (!task?.task_id) return
  registerTaskLabel(task.task_id)
  const idx = knownTasks.value.findIndex((item) => item.task_id === task.task_id)
  const next = {
    ...(idx >= 0 ? knownTasks.value[idx] : {}),
    ...task,
    label: formatTaskLabel(task.task_id),
    log_path: task.log_path || (idx >= 0 ? knownTasks.value[idx]?.log_path : null) || null,
  }
  if (idx >= 0) knownTasks.value.splice(idx, 1, next)
  else knownTasks.value.push(next)
  knownTasks.value = [...knownTasks.value].sort(
    (a, b) => Number(b?.timestamp || b?.created_at || 0) - Number(a?.timestamp || a?.created_at || 0)
  )
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

function addLog(entry) {
  logEntries.value.push(entry)
  if (logEntries.value.length > 500) logEntries.value.splice(0, 100)
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

function updateBenchmark(records) {
  if (!Array.isArray(records)) return
  const byTag = {}
  for (const record of records) {
    if (!record?.tag) continue
    if (!byTag[record.tag]) byTag[record.tag] = { count: 0, total: 0, max: 0 }
    byTag[record.tag].count += 1
    byTag[record.tag].total += record.duration_ms || 0
    byTag[record.tag].max = Math.max(byTag[record.tag].max, record.duration_ms || 0)
  }
  for (const [tag, stats] of Object.entries(byTag)) {
    benchmarkStats[tag] = {
      count: stats.count,
      avg: stats.total / stats.count,
      max: stats.max,
    }
  }
}

if (props.on) {
  props.on('log_entry', (msg) => {
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
    }
  })
  props.on('world_snapshot', (msg) => {
    if (msg.data?.benchmark) updateBenchmark(msg.data.benchmark)
  })
  props.on('benchmark', (msg) => {
    if (msg.data?.records) updateBenchmark(msg.data.records)
  })
  props.on('task_list', (msg) => {
    const tasks = msg.data?.tasks || []
    normalizeTaskCatalog(tasks)
  })
  props.on('task_update', (msg) => {
    const task = msg.data || {}
    if (!task.task_id) return
    mergeKnownTask(task)
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
  })
  props.on('query_response', (msg) => {
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
  })
  props.on('player_notification', (msg) => {
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
  })
  props.on('task_replay', (msg) => {
    const payload = msg.data || {}
    const taskId = payload.task_id
    if (!taskId) return
    replayBundleCache[taskId] = payload.bundle || null
    replayCache[taskId] = Array.isArray(payload.entries)
      ? payload.entries.map((entry) => traceEntryFromLogRecord(entry, taskId, true))
      : []
    replayRequested[taskId] = true
  })
}

onMounted(() => {
  if (props.send) {
    props.send('sync_request')
  }
})

watch(selectedTaskId, (taskId) => {
  if (!props.send || !taskId || taskId === 'ALL') return
  if (replayRequested[taskId]) return
  replayRequested[taskId] = true
  props.send('task_replay_request', { task_id: taskId })
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
.replay-heading {
  font-size: 11px;
  font-weight: 600;
  color: #8b6b13;
  margin-bottom: 4px;
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

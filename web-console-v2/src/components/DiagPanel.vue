<template>
  <div class="diag-panel">
    <h3>Diagnostics</h3>
    <div class="log-filter">
      <button v-for="lvl in ['ALL','INFO','WARN','ERROR']" :key="lvl"
        :class="['filter-btn', { active: filterLevel === lvl }]"
        @click="filterLevel = lvl">{{ lvl }}</button>
    </div>
    <div class="log-stream" ref="logEl">
      <div v-for="(entry, i) in filteredLogs" :key="i" :class="['log-entry', entry.level?.toLowerCase()]">
        <span class="log-time">{{ formatTime(entry.timestamp) }}</span>
        <span class="log-tag">[{{ entry.tag || 'log' }}]</span>
        <span class="log-msg">{{ entry.message }}</span>
      </div>
      <div v-if="!logEntries.length" class="empty">等待日志...</div>
    </div>

    <h3>Benchmark</h3>
    <div class="benchmark-summary">
      <div v-for="(stats, tag) in benchmarkStats" :key="tag" class="bench-row">
        <span class="bench-tag">{{ tag }}</span>
        <span class="bench-count">{{ stats.count }}次</span>
        <span class="bench-avg">avg {{ stats.avg.toFixed(1) }}ms</span>
        <span class="bench-max">max {{ stats.max.toFixed(1) }}ms</span>
      </div>
      <div v-if="!Object.keys(benchmarkStats).length" class="empty">无数据</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, reactive, defineProps } from 'vue'

const props = defineProps({ on: Function })

const logEntries = ref([])
const logEl = ref(null)
const benchmarkStats = reactive({})
const filterLevel = ref('ALL')

const LEVEL_ORDER = { 'DEBUG': 0, 'INFO': 1, 'WARN': 2, 'WARNING': 2, 'ERROR': 3 }
const filteredLogs = computed(() => {
  if (filterLevel.value === 'ALL') return logEntries.value
  const minLevel = LEVEL_ORDER[filterLevel.value] || 0
  return logEntries.value.filter(e => (LEVEL_ORDER[e.level?.toUpperCase()] || 0) >= minLevel)
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString()
}

function addLog(entry) {
  logEntries.value.push(entry)
  if (logEntries.value.length > 500) logEntries.value.splice(0, 100)
  nextTick(() => { if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight })
}

function updateBenchmark(records) {
  if (!Array.isArray(records)) return
  const byTag = {}
  for (const r of records) {
    if (!byTag[r.tag]) byTag[r.tag] = { count: 0, total: 0, max: 0 }
    byTag[r.tag].count++
    byTag[r.tag].total += r.duration_ms || 0
    byTag[r.tag].max = Math.max(byTag[r.tag].max, r.duration_ms || 0)
  }
  for (const [tag, s] of Object.entries(byTag)) {
    benchmarkStats[tag] = { count: s.count, avg: s.total / s.count, max: s.max }
  }
}

if (props.on) {
  props.on('log_entry', (msg) => {
    const entry = msg.data || msg
    addLog({
      level: entry.level || 'INFO',
      tag: entry.component || entry.tag || 'log',
      message: entry.message || JSON.stringify(entry),
      timestamp: entry.timestamp || msg.timestamp,
    })
  })
  props.on('world_snapshot', (msg) => {
    if (msg.data?.benchmark) updateBenchmark(msg.data.benchmark)
  })
  props.on('benchmark', (msg) => {
    if (msg.data?.records) updateBenchmark(msg.data.records)
  })
}
</script>

<style scoped>
.diag-panel { padding: 12px; display: flex; flex-direction: column; height: 100%; }
.diag-panel h3 { margin: 8px 0; font-size: 14px; color: #666; }
.log-filter { display: flex; gap: 4px; margin-bottom: 6px; }
.filter-btn { padding: 2px 8px; border: 1px solid #ccc; border-radius: 3px; background: #f5f5f5; cursor: pointer; font-size: 11px; }
.filter-btn.active { background: #1976d2; color: white; border-color: #1976d2; }
.log-stream { flex: 1; overflow-y: auto; font-family: monospace; font-size: 12px; background: #1e1e1e; color: #d4d4d4; padding: 8px; border-radius: 4px; min-height: 150px; }
.log-entry { margin-bottom: 2px; }
.log-entry.error { color: #f44336; }
.log-entry.warn, .log-entry.warning { color: #ff9800; }
.log-time { color: #888; margin-right: 6px; }
.log-tag { color: #4fc3f7; margin-right: 6px; }
.empty { color: #999; font-size: 13px; }
.benchmark-summary { font-size: 12px; }
.bench-row { display: flex; gap: 12px; padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
.bench-tag { font-weight: bold; min-width: 100px; }
.bench-count { color: #666; }
.bench-avg { color: #2196f3; }
.bench-max { color: #f44336; }
</style>

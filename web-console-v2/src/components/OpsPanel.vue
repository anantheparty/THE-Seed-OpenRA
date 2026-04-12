<template>
  <div class="ops-panel">
    <h3>Operations</h3>
    <div class="vnc-container">
      <div v-if="!vncAvailable" class="vnc-placeholder">
        <span>VNC 未连接</span>
        <small>启动游戏后自动显示</small>
      </div>
      <iframe v-else :src="vncUrl" class="vnc-frame" allowfullscreen></iframe>
    </div>

    <h3>Game Control</h3>
    <div class="game-controls">
      <button @click="restartGame" class="btn-restart">重启游戏</button>
    </div>
    <div class="game-control-note">Web 面板当前仅支持重启对局；启动/停止请走本地进程控制。</div>

    <div class="game-status">
      <span :class="statusClass">
        {{ statusText }}
      </span>
    </div>
    <div v-if="gameStale && (staleFailures || lastRefreshError || gameDisconnected)" class="game-status-detail">
      <span v-if="gameDisconnected">连接状态: 已断开</span>
      <span v-if="staleFailures">连续失败 {{ staleFailures }}<template v-if="failureThreshold"> / {{ failureThreshold }}</template></span>
      <span v-if="lastRefreshError">最近错误: {{ lastRefreshError }}</span>
    </div>
    <div v-if="runtimeFault.degraded" class="game-status-detail runtime-fault-detail">
      <span>
        运行时降级: {{ runtimeFault.source || 'unknown' }}<template v-if="runtimeFault.stage"> / {{ runtimeFault.stage }}</template>
      </span>
      <span v-if="runtimeFault.error">错误: {{ runtimeFault.error }}</span>
    </div>
    <div v-if="capabilityTruthBlocker" class="game-status-detail truth-detail">
      <span>能力真值受限: {{ capabilityTruthText }}</span>
      <span v-if="playerFaction">阵营: {{ playerFaction }}</span>
      <button
        v-if="capabilityTaskId"
        type="button"
        class="diag-link-btn"
        @click="focusCapabilityDiagnostics"
      >查看任务诊断</button>
    </div>
    <div v-if="unitPipelinePreview" class="game-status-detail pipeline-detail">
      <span>能力在途: {{ unitPipelinePreview }}</span>
      <span v-if="unitPipelineFocus.requestCount || unitPipelineFocus.reservationCount">
        请求 {{ unitPipelineFocus.requestCount || 0 }} · 预留 {{ unitPipelineFocus.reservationCount || 0 }}
      </span>
      <span v-if="unitPipelineFocus.status || unitPipelineFocus.remainingCount || unitPipelineFocus.assignedCount || unitPipelineFocus.producedCount || unitPipelineFocus.startReleased || unitPipelineFocus.bootstrapJobId">
        <template v-if="unitPipelineFocus.status">状态 {{ unitPipelineFocus.status }}</template>
        <template v-if="unitPipelineFocus.remainingCount"> · 剩余 {{ unitPipelineFocus.remainingCount }}</template>
        <template v-if="unitPipelineFocus.assignedCount"> · 已分配 {{ unitPipelineFocus.assignedCount }}</template>
        <template v-if="unitPipelineFocus.producedCount"> · 已交付 {{ unitPipelineFocus.producedCount }}</template>
        <template v-if="unitPipelineFocus.startReleased"> · 已释放起始包</template>
        <template v-if="unitPipelineFocus.bootstrapJobId"> · bootstrap={{ unitPipelineFocus.bootstrapJobId }}</template>
      </span>
      <span v-if="unitPipelineFocus.detail">
        当前卡点: {{ unitPipelineFocus.taskLabel ? `#${unitPipelineFocus.taskLabel} · ${unitPipelineFocus.detail}` : unitPipelineFocus.detail }}
      </span>
      <button
        v-if="capabilityTaskId"
        type="button"
        class="diag-link-btn"
        @click="focusCapabilityDiagnostics"
      >定位到能力任务</button>
      <button
        v-if="unitPipelineFocus.taskId && unitPipelineFocus.taskId !== capabilityTaskId"
        type="button"
        class="diag-link-btn"
        @click="focusPipelineTaskDiagnostics"
      >定位到阻塞任务</button>
    </div>

    <h3>Mode</h3>
    <div class="controls">
      <button @click="switchMode('user')">用户模式</button>
      <button @click="switchMode('debug')">调试模式</button>
    </div>

    <div class="connection-status">
      <span :class="connected ? 'online' : 'offline'">
        {{ connected ? '● WS 已连接' : '○ WS 断开' }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  connected: Boolean,
  send: Function,
  on: Function,
})
const emit = defineEmits(['mode-switch'])

const vncUrlParam = new URLSearchParams(window.location.search).get('vnc_url')
const vncUrl = ref(vncUrlParam || '')
const vncAvailable = ref(!!vncUrlParam)
const gameStale = ref(false)
const gameDisconnected = ref(false)
const staleFailures = ref(0)
const failureThreshold = ref(0)
const lastRefreshError = ref('')
const statusText = ref('● 数据正常')
const runtimeFault = ref({
  degraded: false,
  source: '',
  stage: '',
  error: '',
})
const capabilityTruthBlocker = ref('')
const playerFaction = ref('')
const capabilityTruthText = ref('')
const unitPipelinePreview = ref('')
const unitPipelineFocus = ref({
  detail: '',
  taskId: '',
  taskLabel: '',
  requestCount: 0,
  reservationCount: 0,
  status: '',
  remainingCount: 0,
  assignedCount: 0,
  producedCount: 0,
  startReleased: false,
  bootstrapJobId: '',
})
const capabilityTaskId = ref('')

const statusClass = computed(() => {
  if (gameStale.value) return 'stale'
  if (runtimeFault.value.degraded || capabilityTruthBlocker.value || unitPipelineFocus.value.detail) return 'degraded'
  return 'healthy'
})

function formatCapabilityTruthText(blocker, faction) {
  if (blocker === 'faction_roster_unsupported') {
    return `demo capability roster 未覆盖${faction ? ` (${faction})` : '当前阵营'}`
  }
  return blocker || ''
}

function buildStatusText() {
  const staleLabel = gameDisconnected.value
    ? `⚠ 游戏连接断开${staleFailures.value ? ` (${staleFailures.value}${failureThreshold.value ? `/${failureThreshold.value}` : ''})` : ''}`
    : gameStale.value
    ? `⚠ 数据过期${staleFailures.value ? ` (${staleFailures.value}${failureThreshold.value ? `/${failureThreshold.value}` : ''})` : ''}`
    : ''
  let primary = '● 数据正常'
  if (gameStale.value) primary = staleLabel
  else if (runtimeFault.value.degraded) primary = '⚠ 运行降级'
  else if (capabilityTruthBlocker.value) primary = '⚠ 能力真值受限'
  else if (unitPipelineFocus.value.detail) primary = '⚠ 能力管线阻塞'

  const extras = []
  if ((gameStale.value || runtimeFault.value.degraded) && capabilityTruthBlocker.value) {
    extras.push('能力受限')
  }
  if ((gameStale.value || runtimeFault.value.degraded || capabilityTruthBlocker.value) && unitPipelineFocus.value.detail) {
    extras.push('管线阻塞')
  }
  if (gameStale.value && runtimeFault.value.degraded) {
    extras.unshift('运行降级')
  }
  return extras.length ? `${primary} · ${extras.join(' · ')}` : primary
}

function switchMode(mode) {
  if (props.send) props.send('mode_switch', { mode })
  emit('mode-switch', mode)
}

function restartGame() {
  if (props.send) props.send('game_restart', {})
}

function focusCapabilityDiagnostics() {
  if (!capabilityTaskId.value) return
  focusDiagnosticsTask(capabilityTaskId.value)
}

function focusPipelineTaskDiagnostics() {
  if (!unitPipelineFocus.value.taskId) return
  focusDiagnosticsTask(unitPipelineFocus.value.taskId)
}

function focusDiagnosticsTask(taskId) {
  if (!taskId) return
  window.dispatchEvent(
    new CustomEvent('theseed:focus-diagnostics-task', {
      detail: { taskId },
    }),
  )
}

if (props.on) {
  props.on('world_snapshot', (msg) => {
    const data = msg.data || {}
    const runtimeState = data.runtime_state || {}
    const capabilityStatus = runtimeState.capability_status || {}
    gameStale.value = !!data.stale
    gameDisconnected.value = !!data.disconnected
    staleFailures.value = Number(data.consecutive_refresh_failures || 0)
    failureThreshold.value = Number(data.failure_threshold || 0)
    lastRefreshError.value = String(data.last_refresh_error || '')
    const nextRuntimeFault = data.runtime_fault_state || {}
    runtimeFault.value = {
      degraded: !!nextRuntimeFault.degraded,
      source: String(nextRuntimeFault.source || ''),
      stage: String(nextRuntimeFault.stage || ''),
      error: String(nextRuntimeFault.error || ''),
    }
    capabilityTruthBlocker.value = String(data.capability_truth_blocker || '')
    playerFaction.value = String(data.player_faction || '')
    capabilityTruthText.value = formatCapabilityTruthText(capabilityTruthBlocker.value, playerFaction.value)
    unitPipelinePreview.value = String(data.unit_pipeline_preview || '')
    const nextPipelineFocus = data.unit_pipeline_focus || {}
    unitPipelineFocus.value = {
      detail: String(nextPipelineFocus.detail || ''),
      taskId: String(nextPipelineFocus.task_id || ''),
      taskLabel: String(nextPipelineFocus.task_label || ''),
      requestCount: Number(nextPipelineFocus.request_count || 0),
      reservationCount: Number(nextPipelineFocus.reservation_count || 0),
      status: String(nextPipelineFocus.reservation_status || ''),
      remainingCount: Number(nextPipelineFocus.remaining_count || 0),
      assignedCount: Number(nextPipelineFocus.assigned_count || 0),
      producedCount: Number(nextPipelineFocus.produced_count || 0),
      startReleased: !!nextPipelineFocus.start_released,
      bootstrapJobId: String(nextPipelineFocus.bootstrap_job_id || ''),
    }
    capabilityTaskId.value = String(capabilityStatus.task_id || '')
    statusText.value = buildStatusText()
  })
}
</script>

<style scoped>
.ops-panel { padding: 12px; display: flex; flex-direction: column; height: 100%; }
.ops-panel h3 { margin: 12px 0 6px; font-size: 14px; color: #666; }
.ops-panel h3:first-child { margin-top: 0; }
.vnc-container { flex: 1; min-height: 200px; }
.vnc-frame { width: 100%; height: 100%; border: 1px solid #ddd; border-radius: 4px; }
.vnc-placeholder { width: 100%; height: 100%; min-height: 120px; border: 1px dashed #ccc; border-radius: 4px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #999; background: #fafafa; }
.vnc-placeholder small { font-size: 11px; margin-top: 4px; }
.game-controls { display: flex; gap: 6px; }
.game-controls button { padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 12px; }
.btn-restart { background: #fff3e0; border-color: #ff9800; }
.btn-restart:hover { background: #ffe0b2; }
.game-control-note { margin-top: 6px; font-size: 11px; color: #78909c; line-height: 1.4; }
.game-status { margin-top: 6px; font-size: 12px; }
.game-status-detail {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 11px;
  color: #78909c;
  line-height: 1.4;
  word-break: break-word;
}
.truth-detail {
  color: #8a4f00;
}
.runtime-fault-detail {
  color: #b71c1c;
}
.diag-link-btn {
  align-self: flex-start;
  padding: 0;
  border: 0;
  background: transparent;
  color: #1565c0;
  font-size: 11px;
  cursor: pointer;
  text-decoration: underline;
}
.diag-link-btn:hover {
  color: #0d47a1;
}
.healthy { color: #4caf50; }
.stale { color: #ff9800; }
.degraded { color: #e65100; }
.controls { display: flex; gap: 8px; }
.controls button { padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5; cursor: pointer; font-size: 12px; }
.controls button:hover { background: #e0e0e0; }
.connection-status { margin-top: 8px; font-size: 12px; }
.online { color: #4caf50; }
.offline { color: #f44336; }
</style>

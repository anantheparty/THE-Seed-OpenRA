<template>
  <div class="task-panel">
    <div class="panel-header">
      <h3>Tasks</h3>
    </div>
    <div v-if="!tasks.length" class="empty">无活跃任务</div>
    <div v-for="task in tasks" :key="task.task_id" :class="['task-card', task.status, { capability: task.is_capability }]">
      <div class="task-header">
        <button
          class="expand-btn"
          :title="isTaskExpanded(task) ? '折叠任务详情' : '展开任务详情'"
          @click="toggleTaskExpanded(task.task_id)"
        >{{ isTaskExpanded(task) ? '▾' : '▸' }}</button>
        <span v-if="task.is_capability" class="capability-badge">常驻</span>
        <button
          class="diag-focus-btn"
          :title="`在诊断面板中查看 ${displayTaskLabel(task.task_id)}`"
          @click="focusDiagnostics(task)"
        >诊</button>
        <span class="task-id" :title="task.task_id">{{ displayTaskLabel(task.task_id) }}</span>
        <span :class="['status-badge', task.status]">{{ task.status }}</span>
        <button
          v-if="!isTerminalTask(task) && !task.is_capability"
          class="cancel-btn"
          :title="`取消任务 ${displayTaskLabel(task.task_id)}`"
          @click="cancelTask(task)"
        >✕</button>
      </div>
      <div class="task-text">{{ task.raw_text }}</div>
      <div v-if="isTaskExpanded(task)" class="task-details">
        <div class="task-meta">
          优先级: {{ task.priority }} · {{ formatTaskAge(task.timestamp) }}
        </div>
        <div v-if="getTaskStatusLine(task)" class="task-hint">
          {{ getTaskStatusLine(task) }}
        </div>
        <div v-if="getTaskTriageMeta(task).length" class="task-triage-meta">
          <span v-for="item in getTaskTriageMeta(task)" :key="`${task.task_id}-${item}`" class="task-triage-chip">
            {{ item }}
          </span>
        </div>
        <div v-if="task.jobs?.length" class="task-jobs">
          <div class="task-jobs-title">Experts · {{ task.job_count }}</div>
          <div v-for="job in getActiveJobs(task)" :key="job.job_id" class="job-row">
            <span class="job-expert">{{ job.expert_type }}</span>
            <span :class="['job-status', job.status]">{{ job.status }}</span>
          </div>
          <div v-for="job in getActiveJobs(task)" :key="`${job.job_id}-summary`" class="job-summary">
            {{ job.summary }}
          </div>
          <div v-if="getCompletedJobs(task).length" class="completed-jobs">
            <button
              class="completed-jobs-toggle"
              :title="isCompletedJobsExpanded(task) ? '折叠已完成 Expert' : '展开已完成 Expert'"
              @click="toggleCompletedJobs(task.task_id)"
            >
              {{ isCompletedJobsExpanded(task) ? '▾' : '▸' }} 已完成 Experts · {{ getCompletedJobs(task).length }}
            </button>
            <template v-if="isCompletedJobsExpanded(task)">
              <div v-for="job in getCompletedJobs(task)" :key="`done-${job.job_id}`" class="job-row completed">
                <span class="job-expert">{{ job.expert_type }}</span>
                <span :class="['job-status', job.status]">{{ job.status }}</span>
              </div>
              <div v-for="job in getCompletedJobs(task)" :key="`done-${job.job_id}-summary`" class="job-summary completed">
                {{ job.summary }}
              </div>
            </template>
          </div>
        </div>
      </div>
      <div v-else-if="getTaskStatusLine(task)" class="task-collapsed-hint">
        {{ getTaskStatusLine(task) }}
      </div>
    </div>

    <h3 v-if="pendingQuestions.length">待回答</h3>
    <div v-for="q in pendingQuestions" :key="q.message_id" class="question-card">
      <div class="question-text">{{ q.question }}</div>
      <div class="question-options">
        <button v-for="opt in q.options" :key="opt" @click="reply(q, opt)" class="option-btn">{{ opt }}</button>
      </div>
      <div class="question-meta">超时: {{ q.timeout_s }}s · 默认: {{ q.default_option }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useTimeAgo } from '../composables/useTimeAgo.js'
import {
  clearTaskUiState,
  formatTaskLabel,
  loadHiddenTaskIds,
  registerTaskLabels,
  saveHiddenTaskIds,
} from '../composables/taskLabels.js'

const props = defineProps({
  send: Function,
  on: Function,
})

const tasks = ref([])
const pendingQuestions = ref([])
const hiddenTaskIds = ref(loadHiddenTaskIds())
const expandedTaskState = ref({})
const completedJobsExpandedState = ref({})
const { tick, formatTimeAgo } = useTimeAgo()
let latestTaskList = []
let clearUiHandler = null

function sortTasksNewestFirst(items) {
  return [...items].sort((a, b) => {
    // Capability tasks always at top
    if (a?.is_capability && !b?.is_capability) return -1
    if (!a?.is_capability && b?.is_capability) return 1
    const aTime = Number(a?.timestamp || a?.created_at || 0)
    const bTime = Number(b?.timestamp || b?.created_at || 0)
    return bTime - aTime
  })
}

function isTerminalTask(task) {
  return ['succeeded', 'failed', 'aborted', 'partial'].includes(task?.status)
}

function isTerminalJob(job) {
  return ['succeeded', 'failed', 'aborted', 'partial'].includes(job?.status)
}

function normalizeTasks(items) {
  registerTaskLabels(items)
  const hidden = hiddenTaskIds.value
  return sortTasksNewestFirst(
    (items || []).filter((task) => task.is_capability || !hidden.has(task.task_id) || !isTerminalTask(task))
  )
}

function displayTaskLabel(taskId) {
  return formatTaskLabel(taskId)
}

function formatTaskAge(timestamp) {
  void tick.value
  return formatTimeAgo(timestamp)
}

function isTaskExpanded(task) {
  const explicit = expandedTaskState.value[task.task_id]
  if (typeof explicit === 'boolean') return explicit
  return !isTerminalTask(task)
}

function toggleTaskExpanded(taskId) {
  const task = latestTaskList.find((item) => item.task_id === taskId)
  if (!task) return
  expandedTaskState.value = {
    ...expandedTaskState.value,
    [taskId]: !isTaskExpanded(task),
  }
}

function getActiveJobs(task) {
  return (task?.jobs || []).filter((job) => !isTerminalJob(job))
}

function getCompletedJobs(task) {
  return (task?.jobs || []).filter((job) => isTerminalJob(job))
}

function isCompletedJobsExpanded(task) {
  return completedJobsExpandedState.value[task.task_id] === true
}

function toggleCompletedJobs(taskId) {
  const task = latestTaskList.find((item) => item.task_id === taskId)
  if (!task || !getCompletedJobs(task).length) return
  completedJobsExpandedState.value = {
    ...completedJobsExpandedState.value,
    [taskId]: !isCompletedJobsExpanded(task),
  }
}

function clearHistory() {
  const nextHidden = new Set()
  for (const task of latestTaskList) {
    if (isTerminalTask(task)) nextHidden.add(task.task_id)
  }
  clearTaskUiState()
  hiddenTaskIds.value = nextHidden
  expandedTaskState.value = {}
  completedJobsExpandedState.value = {}
  saveHiddenTaskIds(nextHidden)
  tasks.value = normalizeTasks(latestTaskList)
}

function reply(question, answer) {
  props.send('question_reply', {
    message_id: question.message_id,
    task_id: question.task_id,
    answer: answer,
  })
}

function cancelTask(task) {
  props.send('command_cancel', { task_id: task.task_id })
}

function focusDiagnostics(task) {
  if (!task?.task_id) return
  window.dispatchEvent(
    new CustomEvent('theseed:focus-diagnostics-task', {
      detail: { taskId: task.task_id },
    }),
  )
}

function getLegacyTaskWaitingHint(task) {
  const jobs = task?.jobs || []
  if (!jobs.length) return ''

  const combined = jobs
    .map((job) => `${job?.status || ''} ${job?.summary || ''} ${job?.expert_type || ''}`)
    .join(' ')
    .toLowerCase()

  const hasWaitingJob = jobs.some((job) => job?.status === 'waiting')
  const hasCapabilityWait = /request_units|capability/.test(combined)
  const hasResourceWait = /resource_lost|missing .*resource|waiting for replacement/.test(combined)
  const hasBlockedWait = /waiting|blocked/.test(combined)

  if (!hasWaitingJob && !hasCapabilityWait && !hasResourceWait && !hasBlockedWait) return ''
  if (hasCapabilityWait) return '任务正在等待能力模块完成前置请求，仍在运行中'
  if (hasResourceWait) return '任务正在等待资源补位或恢复，仍在运行中'
  return '任务正在等待执行条件满足，仍在运行中'
}

function getTaskStatusLine(task) {
  if (task?.triage?.status_line) return task.triage.status_line
  return getLegacyTaskWaitingHint(task)
}

function formatWorldSyncError(error) {
  const text = String(error || '').trim()
  if (!text) return ''
  return text.length > 32 ? `${text.slice(0, 29)}...` : text
}

function getTaskTriageMeta(task) {
  const triage = task?.triage || {}
  const items = []
  if (triage.waiting_reason) items.push(`waiting=${triage.waiting_reason}`)
  if (triage.blocking_reason) items.push(`blocker=${triage.blocking_reason}`)
  const reservationCount = Array.isArray(triage.reservation_ids) ? triage.reservation_ids.length : 0
  if (reservationCount) items.push(`reservations=${reservationCount}`)
  if (triage.reservation_preview) items.push(`reservation=${triage.reservation_preview}`)
  if (triage.reservation_status) items.push(`res_status=${triage.reservation_status}`)
  if (Number.isFinite(Number(triage.remaining_count)) && Number(triage.remaining_count) > 0) {
    items.push(`remaining=${Number(triage.remaining_count)}`)
  }
  if (Number.isFinite(Number(triage.assigned_count)) && Number(triage.assigned_count) > 0) {
    items.push(`assigned=${Number(triage.assigned_count)}`)
  }
  if (Number.isFinite(Number(triage.produced_count)) && Number(triage.produced_count) > 0) {
    items.push(`produced=${Number(triage.produced_count)}`)
  }
  if (triage.start_released) items.push('start_released=yes')
  if (triage.bootstrap_job_id) items.push(`bootstrap=${triage.bootstrap_job_id}`)
  if (triage.has_active_group || Number(triage.active_group_size || 0) > 0) {
    items.push(`owned=${Number(triage.active_group_size || 0)}`)
  }
  if (triage.world_stale) items.push('world=stale')
  if (triage.world_sync_failures) {
    const threshold = Number(triage.world_sync_failure_threshold || 0)
    items.push(`sync_fail=${triage.world_sync_failures}${threshold ? `/${threshold}` : ''}`)
  }
  const syncError = formatWorldSyncError(triage.world_sync_error)
  if (syncError) items.push(`sync=${syncError}`)
  return items
}

if (props.on) {
  props.on('task_list', (msg) => {
    latestTaskList = msg.data?.tasks || []
    tasks.value = normalizeTasks(latestTaskList)
    pendingQuestions.value = msg.data?.pending_questions || []
  })
  props.on('task_update', (msg) => {
    const update = msg.data
    if (!update?.task_id) return
    const idx = latestTaskList.findIndex(t => t.task_id === update.task_id)
    if (idx >= 0) Object.assign(latestTaskList[idx], update)
    else latestTaskList.push(update)
    tasks.value = normalizeTasks(latestTaskList)
  })
  props.on('world_snapshot', (msg) => {
    if (msg.data?.pending_questions) {
      pendingQuestions.value = msg.data.pending_questions
    }
  })
}

onMounted(() => {
  clearUiHandler = () => clearHistory()
  window.addEventListener('theseed:clear-ui', clearUiHandler)
})

onUnmounted(() => {
  if (clearUiHandler) window.removeEventListener('theseed:clear-ui', clearUiHandler)
})
</script>

<style scoped>
.task-panel { padding: 12px; overflow-y: auto; }
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.task-panel h3 { margin: 8px 0; font-size: 14px; color: #666; }
.empty { color: #999; font-size: 13px; }
.task-card { border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.task-card.running { border-left: 3px solid #4caf50; }
.task-card.pending { border-left: 3px solid #ff9800; }
.task-card.succeeded { border-left: 3px solid #2196f3; opacity: 0.6; }
.task-card.failed { border-left: 3px solid #f44336; opacity: 0.6; }
.task-card.capability { border: 1px solid #7c4dff; border-left: 3px solid #7c4dff; background: #f3f0ff; }
.capability-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  background: #7c4dff;
  color: white;
  font-weight: 600;
  flex-shrink: 0;
}
.task-header { display: flex; justify-content: space-between; align-items: center; gap: 4px; }
.expand-btn {
  padding: 0;
  border: none;
  background: none;
  color: #607d8b;
  font-size: 12px;
  cursor: pointer;
  line-height: 1;
  flex-shrink: 0;
}
.expand-btn:hover { color: #263238; }
.task-details { margin-top: 4px; }
.cancel-btn {
  margin-left: auto;
  padding: 1px 6px;
  background: none;
  border: 1px solid #f44336;
  border-radius: 3px;
  color: #f44336;
  font-size: 11px;
  cursor: pointer;
  line-height: 1.4;
  flex-shrink: 0;
}
.cancel-btn:hover { background: #ffebee; }
.task-id { font-size: 11px; color: #999; }
.status-badge { font-size: 11px; padding: 2px 6px; border-radius: 3px; }
.status-badge.running { background: #e8f5e9; color: #2e7d32; }
.status-badge.pending { background: #fff3e0; color: #e65100; }
.task-text { margin: 4px 0; font-size: 13px; }
.task-meta { font-size: 11px; color: #999; }
.task-hint {
  margin-top: 4px;
  padding: 4px 6px;
  border-radius: 4px;
  background: #fff8e1;
  color: #8a6d3b;
  font-size: 11px;
  line-height: 1.4;
}
.task-triage-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.task-triage-chip {
  font-size: 10px;
  color: #546e7a;
  background: #eef3f6;
  border-radius: 999px;
  padding: 2px 6px;
}
.task-collapsed-hint {
  margin-top: 4px;
  color: #78909c;
  font-size: 11px;
  line-height: 1.4;
}
.task-jobs {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #eceff1;
}
.task-jobs-title {
  font-size: 11px;
  color: #607d8b;
  margin-bottom: 6px;
}
.job-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 4px;
}
.job-row.completed {
  opacity: 0.78;
}
.job-expert {
  font-weight: 600;
  color: #37474f;
}
.job-status {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 999px;
  background: #eceff1;
  color: #455a64;
}
.job-status.running { background: #e8f5e9; color: #2e7d32; }
.job-status.waiting { background: #fff3e0; color: #e65100; }
.job-status.succeeded { background: #e3f2fd; color: #1565c0; }
.job-status.failed,
.job-status.aborted { background: #ffebee; color: #c62828; }
.job-summary {
  font-size: 11px;
  color: #78909c;
  margin-bottom: 4px;
}
.job-summary.completed {
  color: #90a4ae;
}
.completed-jobs {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #f1f3f4;
}
.completed-jobs-toggle {
  padding: 0;
  border: none;
  background: none;
  color: #607d8b;
  font-size: 11px;
  cursor: pointer;
  line-height: 1.4;
}
.completed-jobs-toggle:hover {
  color: #263238;
}
.diag-focus-btn {
  width: 22px;
  height: 22px;
  border: 1px solid #d0d7de;
  border-radius: 4px;
  background: #fff7db;
  color: #7a5200;
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  padding: 0;
}
.diag-focus-btn:hover {
  background: #ffefb2;
}
.question-card { border: 1px solid #ff9800; border-radius: 6px; padding: 8px; margin-bottom: 8px; background: #fff8e1; }
.question-text { font-size: 13px; margin-bottom: 6px; }
.question-options { display: flex; gap: 6px; }
.option-btn { padding: 4px 12px; border: 1px solid #ff9800; border-radius: 4px; background: white; cursor: pointer; font-size: 12px; }
.option-btn:hover { background: #fff3e0; }
.question-meta { font-size: 11px; color: #999; margin-top: 4px; }
</style>

<template>
  <div class="task-panel">
    <div class="panel-header">
      <h3>Tasks</h3>
    </div>
    <div v-if="!tasks.length" class="empty">无活跃任务</div>
    <div v-for="task in tasks" :key="task.task_id" :class="['task-card', task.status]">
      <div class="task-header">
        <span class="task-id" :title="task.task_id">{{ displayTaskLabel(task.task_id) }}</span>
        <span :class="['status-badge', task.status]">{{ task.status }}</span>
        <button
          v-if="!isTerminalTask(task)"
          class="cancel-btn"
          :title="`取消任务 ${displayTaskLabel(task.task_id)}`"
          @click="cancelTask(task)"
        >✕</button>
      </div>
      <div class="task-text">{{ task.raw_text }}</div>
      <div class="task-meta">
        优先级: {{ task.priority }} · {{ formatTimeAgo(task.timestamp) }}
      </div>
      <div v-if="task.jobs?.length" class="task-jobs">
        <div class="task-jobs-title">Experts · {{ task.job_count }}</div>
        <div v-for="job in task.jobs" :key="job.job_id" class="job-row">
          <span class="job-expert">{{ job.expert_type }}</span>
          <span :class="['job-status', job.status]">{{ job.status }}</span>
        </div>
        <div v-for="job in task.jobs" :key="`${job.job_id}-summary`" class="job-summary">
          {{ job.summary }}
        </div>
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
import { ref, defineProps, onMounted, onUnmounted } from 'vue'
import { formatTimeAgo } from '../composables/useTimeAgo.js'
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
let latestTaskList = []
let clearUiHandler = null

function sortTasksNewestFirst(items) {
  return [...items].sort((a, b) => {
    const aTime = Number(a?.timestamp || a?.created_at || 0)
    const bTime = Number(b?.timestamp || b?.created_at || 0)
    return bTime - aTime
  })
}

function isTerminalTask(task) {
  return ['succeeded', 'failed', 'aborted', 'partial'].includes(task?.status)
}

function normalizeTasks(items) {
  registerTaskLabels(items)
  const hidden = hiddenTaskIds.value
  return sortTasksNewestFirst(
    (items || []).filter((task) => !hidden.has(task.task_id) || !isTerminalTask(task))
  )
}

function displayTaskLabel(taskId) {
  return formatTaskLabel(taskId)
}

function clearHistory() {
  const nextHidden = new Set()
  for (const task of latestTaskList) {
    if (isTerminalTask(task)) nextHidden.add(task.task_id)
  }
  clearTaskUiState()
  hiddenTaskIds.value = nextHidden
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
.task-header { display: flex; justify-content: space-between; align-items: center; gap: 4px; }
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
.question-card { border: 1px solid #ff9800; border-radius: 6px; padding: 8px; margin-bottom: 8px; background: #fff8e1; }
.question-text { font-size: 13px; margin-bottom: 6px; }
.question-options { display: flex; gap: 6px; }
.option-btn { padding: 4px 12px; border: 1px solid #ff9800; border-radius: 4px; background: white; cursor: pointer; font-size: 12px; }
.option-btn:hover { background: #fff3e0; }
.question-meta { font-size: 11px; color: #999; margin-top: 4px; }
</style>

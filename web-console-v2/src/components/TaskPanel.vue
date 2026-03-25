<template>
  <div class="task-panel">
    <div class="panel-header">
      <h3>Tasks</h3>
      <button
        v-if="hasTerminalTasks"
        @click="clearHistory"
        class="clear-btn"
        title="只隐藏已结束任务，不会取消正在运行的任务"
      >
        隐藏已结束
      </button>
    </div>
    <div v-if="!tasks.length" class="empty">无活跃任务</div>
    <div v-for="task in tasks" :key="task.task_id" :class="['task-card', task.status]">
      <div class="task-header">
        <span class="task-id" :title="task.task_id">{{ displayTaskLabel(task.task_id) }}</span>
        <span :class="['status-badge', task.status]">{{ task.status }}</span>
      </div>
      <div class="task-text">{{ task.raw_text }}</div>
      <div class="task-meta">
        优先级: {{ task.priority }} · {{ formatTimeAgo(task.timestamp) }}
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
import { computed, ref, defineProps } from 'vue'
import { formatTimeAgo } from '../composables/useTimeAgo.js'
import {
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

const hasTerminalTasks = computed(() => tasks.value.some(isTerminalTask))

function clearHistory() {
  const nextHidden = new Set(hiddenTaskIds.value)
  for (const task of tasks.value) {
    if (isTerminalTask(task)) nextHidden.add(task.task_id)
  }
  hiddenTaskIds.value = nextHidden
  saveHiddenTaskIds(nextHidden)
  tasks.value = normalizeTasks(tasks.value)
}

function reply(question, answer) {
  props.send('question_reply', {
    message_id: question.message_id,
    task_id: question.task_id,
    answer: answer,
  })
}

if (props.on) {
  props.on('task_list', (msg) => {
    tasks.value = normalizeTasks(msg.data?.tasks || [])
    pendingQuestions.value = msg.data?.pending_questions || []
  })
  props.on('task_update', (msg) => {
    const update = msg.data
    if (!update?.task_id) return
    const idx = tasks.value.findIndex(t => t.task_id === update.task_id)
    if (idx >= 0) Object.assign(tasks.value[idx], update)
    else tasks.value.push(update)
    tasks.value = normalizeTasks(tasks.value)
  })
  props.on('world_snapshot', (msg) => {
    if (msg.data?.pending_questions) {
      pendingQuestions.value = msg.data.pending_questions
    }
  })
}
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
.clear-btn {
  padding: 3px 8px;
  border: 1px solid #d0d7de;
  border-radius: 999px;
  background: #fff;
  color: #555;
  cursor: pointer;
  font-size: 11px;
}
.clear-btn:hover { background: #f6f8fa; }
.task-card { border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.task-card.running { border-left: 3px solid #4caf50; }
.task-card.pending { border-left: 3px solid #ff9800; }
.task-card.succeeded { border-left: 3px solid #2196f3; opacity: 0.6; }
.task-card.failed { border-left: 3px solid #f44336; opacity: 0.6; }
.task-header { display: flex; justify-content: space-between; align-items: center; }
.task-id { font-size: 11px; color: #999; }
.status-badge { font-size: 11px; padding: 2px 6px; border-radius: 3px; }
.status-badge.running { background: #e8f5e9; color: #2e7d32; }
.status-badge.pending { background: #fff3e0; color: #e65100; }
.task-text { margin: 4px 0; font-size: 13px; }
.task-meta { font-size: 11px; color: #999; }
.question-card { border: 1px solid #ff9800; border-radius: 6px; padding: 8px; margin-bottom: 8px; background: #fff8e1; }
.question-text { font-size: 13px; margin-bottom: 6px; }
.question-options { display: flex; gap: 6px; }
.option-btn { padding: 4px 12px; border: 1px solid #ff9800; border-radius: 4px; background: white; cursor: pointer; font-size: 12px; }
.option-btn:hover { background: #fff3e0; }
.question-meta { font-size: 11px; color: #999; margin-top: 4px; }
</style>

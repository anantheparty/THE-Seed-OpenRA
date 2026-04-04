<template>
  <div class="chat-view">
    <div class="chat-messages" ref="messagesEl">
      <div v-for="msg in chatMessages" :key="msg.id" :class="['chat-msg', msg.from]">
        <span class="msg-label">{{ msg.label }}</span>
        <span class="msg-content">{{ msg.content }}</span>
        <span class="msg-time">{{ refreshTick.value >= 0 ? formatTimeAgo(msg.timestamp) : '' }}</span>
        <div v-if="msg.options && msg.options.length" class="msg-options">
          <button
            v-for="opt in msg.options"
            :key="opt"
            :disabled="msg.answered"
            @click="replyToQuestion(msg, opt)"
            class="option-btn"
          >{{ opt }}</button>
        </div>
      </div>
    </div>
    <div class="chat-input">
      <input v-model="inputText" @keyup.enter="sendMessage" placeholder="输入指令或提问..." :disabled="!connected" />
      <button
        class="mic-btn"
        :class="{ recording: isRecording }"
        :disabled="!connected || asrLoading"
        :title="isRecording ? '停止录音' : '语音输入'"
        @click="toggleRecording"
      >{{ isRecording ? '⏹' : asrLoading ? '…' : '🎤' }}</button>
      <button @click="sendMessage" :disabled="!connected || !inputText.trim()">发送</button>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, defineProps, onMounted, onUnmounted } from 'vue'
import { formatTimeAgo } from '../composables/useTimeAgo.js'
import { formatTaskLabel, registerTaskLabel, replaceTaskIdsWithLabels } from '../composables/taskLabels.js'

const refreshTick = ref(0)
let refreshTimer = null
onMounted(() => { refreshTimer = setInterval(() => refreshTick.value++, 1000) })
onUnmounted(() => clearInterval(refreshTimer))

const props = defineProps({
  connected: Boolean,
  send: Function,
  on: Function,
})

const STORAGE_KEY = 'theseed_chat_history_session'
const MAX_STORED = 100

const inputText = ref('')
const messagesEl = ref(null)
const isRecording = ref(false)
const asrLoading = ref(false)
let msgId = 0
let _mediaRecorder = null
let _audioChunks = []

// --- ASR (DashScope via backend /api/asr) ---

function _asrBaseUrl() {
  const { protocol, hostname, port } = window.location
  const p = port ? `:${port}` : (protocol === 'https:' ? '' : ':8765')
  return `${protocol}//${hostname}${p}`
}

async function toggleRecording() {
  if (isRecording.value) {
    _mediaRecorder?.stop()
    return
  }
  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (e) {
    addMessage('notification', 'ℹ', `麦克风权限被拒绝: ${e.message}`)
    return
  }
  _audioChunks = []
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : 'audio/webm'
  _mediaRecorder = new MediaRecorder(stream, { mimeType })
  _mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) _audioChunks.push(e.data) }
  _mediaRecorder.onstop = async () => {
    isRecording.value = false
    stream.getTracks().forEach(t => t.stop())
    asrLoading.value = true
    try {
      const blob = new Blob(_audioChunks, { type: mimeType })
      const form = new FormData()
      form.append('audio', blob, 'recording.webm')
      const resp = await fetch(`${_asrBaseUrl()}/api/asr?format=wav&sample_rate=16000`, {
        method: 'POST',
        body: form,
      })
      const json = await resp.json()
      if (json.ok && json.text) {
        inputText.value = json.text
      } else {
        addMessage('notification', '⚠', `语音识别失败: ${json.error || '无结果'}`)
      }
    } catch (e) {
      addMessage('notification', '⚠', `语音识别请求失败: ${e.message}`)
    } finally {
      asrLoading.value = false
    }
  }
  _mediaRecorder.start()
  isRecording.value = true
}

// --- TTS (DashScope via backend /api/tts) ---

let _ttsEnabled = false  // disabled by default; toggle via console: window.__ttsOn = true

async function playTts(text) {
  if (!_ttsEnabled && !window.__ttsOn) return
  try {
    const resp = await fetch(`${_asrBaseUrl()}/api/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, format: 'mp3' }),
    })
    if (!resp.ok) return
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    audio.onended = () => URL.revokeObjectURL(url)
    audio.play()
  } catch (_) { /* TTS failure is non-fatal */ }
}

// Restore from localStorage
function loadHistory() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) {
      const items = JSON.parse(raw)
      msgId = items.length
      return items
    }
  } catch (e) { /* ignore */ }
  return []
}

const chatMessages = ref(loadHistory())

function saveHistory() {
  try {
    const recent = chatMessages.value.slice(-MAX_STORED)
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(recent))
  } catch (e) { /* ignore */ }
}

function addMessage(from, label, content, timestamp, extra = {}) {
  chatMessages.value.push({ id: ++msgId, from, label, content, timestamp: timestamp || Date.now() / 1000, ...extra })
  saveHistory()
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

function replyToQuestion(msg, answer) {
  if (msg.answered) return
  msg.answered = true
  props.send('question_reply', {
    message_id: msg.message_id,
    task_id: msg.task_id,
    answer,
  })
}

function clearChat() {
  chatMessages.value = []
  msgId = 0
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch (_) {
    // ignore storage failures
  }
}

function sendMessage() {
  const text = inputText.value.trim()
  if (!text) return
  const sent = props.send ? props.send('command_submit', { text }) : false
  if (!sent) return
  addMessage('player', '玩家', text)
  inputText.value = ''
}

let offQueryResponse = null
let offPlayerNotification = null
let offTaskMessage = null
let clearUiHandler = null

const _TASK_MSG_LABEL = {
  task_info: 'ℹ 任务',
  task_warning: '⚠ 任务',
  task_question: '? 任务',
  task_complete_report: '✓ 任务',
}

onMounted(() => {
  if (!props.on) return
  offQueryResponse = props.on('query_response', (msg) => {
    const taskId = msg.data?.task_id
    if (taskId) registerTaskLabel(taskId)
    let content = msg.data?.answer || msg.data?.response_text || JSON.stringify(msg.data)
    content = replaceTaskIdsWithLabels(content)
    if (
      taskId &&
      !content.includes(formatTaskLabel(taskId)) &&
      msg.data?.response_type === 'command'
    ) {
      content = `${content}（${formatTaskLabel(taskId)}）`
    }
    addMessage('system', '副官', content, msg.timestamp)
  })
  offPlayerNotification = props.on('player_notification', (msg) => {
    const icon = msg.data?.icon || 'ℹ'
    addMessage('notification', icon, msg.data?.content || JSON.stringify(msg.data), msg.timestamp)
  })
  offTaskMessage = props.on('task_message', (msg) => {
    const d = msg.data || {}
    const label = _TASK_MSG_LABEL[d.type] || 'ℹ 任务'
    const from = d.type === 'task_warning' ? 'task-warning' : d.type === 'task_question' ? 'task-question' : 'task-info'
    const extra = d.options ? {
      options: d.options,
      message_id: d.message_id,
      task_id: d.task_id,
      answered: false,
    } : {}
    const content = d.content || JSON.stringify(d)
    addMessage(from, label, content, msg.timestamp, extra)
    if (['task_info', 'task_warning', 'task_complete_report'].includes(d.type)) {
      playTts(content)
    }
  })
  // task_update is handled by TaskPanel, not ChatView

  clearUiHandler = () => clearChat()
  window.addEventListener('theseed:clear-ui', clearUiHandler)
})

onUnmounted(() => {
  if (offQueryResponse) offQueryResponse()
  if (offPlayerNotification) offPlayerNotification()
  if (offTaskMessage) offTaskMessage()
  if (clearUiHandler) window.removeEventListener('theseed:clear-ui', clearUiHandler)
})
</script>

<style scoped>
.chat-view { display: flex; flex-direction: column; height: 100%; }
.chat-messages { flex: 1; overflow-y: auto; padding: 12px; }
.chat-msg { margin-bottom: 8px; padding: 6px 10px; border-radius: 6px; font-size: 14px; }
.chat-msg.player {
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: 8px;
  background: #e3f2fd;
}
.chat-msg.player .msg-label {
  order: 3;
  margin-right: 0;
}
.chat-msg.player .msg-content {
  order: 2;
}
.chat-msg.player .msg-time {
  order: 1;
  margin-left: 0;
}
.chat-msg.system { background: #f5f5f5; }
.chat-msg.notification { background: #fff3e0; }
.chat-msg.task-info { background: #e8f4fd; border-left: 3px solid #1976d2; }
.chat-msg.task-warning { background: #fff8e1; border-left: 3px solid #f57c00; }
.chat-msg.task-question { background: #f3e5f5; border-left: 3px solid #7b1fa2; }
.msg-label { font-weight: bold; margin-right: 8px; }
.msg-time { font-size: 11px; color: #999; margin-left: 8px; }
.msg-content { white-space: pre-wrap; }
.msg-options { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.option-btn { padding: 4px 12px; background: #7b1fa2; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
.option-btn:hover:not(:disabled) { background: #6a1b9a; }
.option-btn:disabled { background: #ccc; cursor: not-allowed; }
.chat-input { display: flex; padding: 8px; border-top: 1px solid #ddd; }
.chat-input input { flex: 1; padding: 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
.chat-input button { margin-left: 8px; padding: 8px 16px; background: #1976d2; color: white; border: none; border-radius: 4px; cursor: pointer; }
.chat-input button:disabled { background: #ccc; cursor: not-allowed; }
.mic-btn { padding: 8px 10px; background: #f5f5f5; color: #333; border: 1px solid #ccc; }
.mic-btn:hover:not(:disabled) { background: #e0e0e0; }
.mic-btn.recording { background: #ffebee; border-color: #f44336; color: #f44336; animation: pulse 1s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>

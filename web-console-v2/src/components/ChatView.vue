<template>
  <div class="chat-view">
    <div class="chat-messages" ref="messagesEl">
      <div v-for="msg in chatMessages" :key="msg.id" :class="['chat-msg', msg.from]">
        <span class="msg-label">{{ msg.label }}</span>
        <span class="msg-content">{{ msg.content }}</span>
        <span class="msg-time">{{ refreshTick.value >= 0 ? formatTimeAgo(msg.timestamp) : '' }}</span>
      </div>
    </div>
    <div class="chat-input">
      <input v-model="inputText" @keyup.enter="sendMessage" placeholder="输入指令或提问..." :disabled="!connected" />
      <button @click="sendMessage" :disabled="!connected || !inputText.trim()">发送</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, defineProps, onMounted, onUnmounted } from 'vue'
import { formatTimeAgo } from '../composables/useTimeAgo.js'

const refreshTick = ref(0)
let refreshTimer = null
onMounted(() => { refreshTimer = setInterval(() => refreshTick.value++, 1000) })
onUnmounted(() => clearInterval(refreshTimer))

const props = defineProps({
  connected: Boolean,
  send: Function,
  on: Function,
})

const STORAGE_KEY = 'theseed_chat_history'
const MAX_STORED = 100

const inputText = ref('')
const messagesEl = ref(null)
let msgId = 0

// Restore from localStorage
function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(recent))
  } catch (e) { /* ignore */ }
}

function addMessage(from, label, content, timestamp) {
  chatMessages.value.push({ id: ++msgId, from, label, content, timestamp: timestamp || Date.now() / 1000 })
  saveHistory()
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

function sendMessage() {
  const text = inputText.value.trim()
  if (!text) return
  addMessage('player', '玩家', text)
  props.send('command_submit', { text })
  inputText.value = ''
}

if (props.on) {
  props.on('query_response', (msg) => {
    addMessage('system', '副官', msg.data?.answer || msg.data?.response_text || JSON.stringify(msg.data), msg.timestamp)
  })
  props.on('player_notification', (msg) => {
    const icon = msg.data?.icon || 'ℹ'
    addMessage('notification', icon, msg.data?.content || JSON.stringify(msg.data), msg.timestamp)
  })
  // task_update is handled by TaskPanel, not ChatView
}
</script>

<style scoped>
.chat-view { display: flex; flex-direction: column; height: 100%; }
.chat-messages { flex: 1; overflow-y: auto; padding: 12px; }
.chat-msg { margin-bottom: 8px; padding: 6px 10px; border-radius: 6px; font-size: 14px; }
.chat-msg.player { background: #e3f2fd; text-align: right; }
.chat-msg.system { background: #f5f5f5; }
.chat-msg.notification { background: #fff3e0; }
.msg-label { font-weight: bold; margin-right: 8px; }
.msg-time { font-size: 11px; color: #999; margin-left: 8px; }
.msg-content { white-space: pre-wrap; }
.chat-input { display: flex; padding: 8px; border-top: 1px solid #ddd; }
.chat-input input { flex: 1; padding: 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
.chat-input button { margin-left: 8px; padding: 8px 16px; background: #1976d2; color: white; border: none; border-radius: 4px; cursor: pointer; }
.chat-input button:disabled { background: #ccc; cursor: not-allowed; }
</style>

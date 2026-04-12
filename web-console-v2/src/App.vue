<template>
  <div class="app" :class="mode">
    <header class="app-header">
      <h1>THE Seed OpenRA</h1>
      <div class="header-controls">
        <span :class="statusClass">
          {{ statusText }}
        </span>
        <button
          v-if="mode === 'user'"
          @click="toggleOps"
          class="mode-btn"
        >
          {{ opsVisible ? '隐藏操作' : '显示操作' }}
        </button>
        <button @click="clearUi" class="mode-btn" title="清空当前这一局的前后端会话记忆">
          清空界面
        </button>
        <button @click="toggleMode" class="mode-btn">
          {{ mode === 'user' ? '切换调试' : '切换用户' }}
        </button>
      </div>
    </header>

    <div class="app-body">
      <aside class="sidebar-left">
        <TaskPanel :send="send" :on="on" />
      </aside>

      <main class="main-chat">
        <ChatView :connected="connected" :send="send" :on="on" />
      </main>

      <aside v-if="showRightSidebar" class="sidebar-right">
        <OpsPanel v-if="mode === 'user'" :connected="connected" :send="send" :on="on" @mode-switch="setMode" />
        <DiagPanel v-else :send="send" :on="on" />
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useWebSocket } from './composables/useWebSocket.js'
import ChatView from './components/ChatView.vue'
import TaskPanel from './components/TaskPanel.vue'
import OpsPanel from './components/OpsPanel.vue'
import DiagPanel from './components/DiagPanel.vue'

const { connected, reconnecting, send, on } = useWebSocket()
const mode = ref('user')
const opsVisible = ref(false)
const statusText = computed(() => {
  if (reconnecting.value) return '◌ 重连中'
  return connected.value ? '● 已连接' : '○ 断开'
})
const statusClass = computed(() => {
  if (reconnecting.value) return 'status-warn'
  return connected.value ? 'status-on' : 'status-off'
})
const showRightSidebar = computed(() => mode.value === 'debug' || opsVisible.value)

function toggleMode() {
  mode.value = mode.value === 'user' ? 'debug' : 'user'
  if (mode.value === 'user') opsVisible.value = false
  send('mode_switch', { mode: mode.value })
}

function toggleOps() {
  opsVisible.value = !opsVisible.value
}

function clearUi() {
  send('session_clear')
}

function setMode(m) {
  mode.value = m
  if (m === 'user') opsVisible.value = false
}

on('session_cleared', () => {
  window.dispatchEvent(new CustomEvent('theseed:clear-ui'))
})

let focusDiagnosticsHandler = null

onMounted(() => {
  focusDiagnosticsHandler = (event) => {
    const taskId = event?.detail?.taskId
    if (!taskId) return
    mode.value = 'debug'
    opsVisible.value = false
    nextTick(() => {
      window.dispatchEvent(
        new CustomEvent('theseed:apply-diagnostics-focus', {
          detail: { taskId },
        }),
      )
    })
  }
  window.addEventListener('theseed:focus-diagnostics-task', focusDiagnosticsHandler)
})

onUnmounted(() => {
  if (focusDiagnosticsHandler) {
    window.removeEventListener('theseed:focus-diagnostics-task', focusDiagnosticsHandler)
  }
})
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fafafa; }

.app { display: flex; flex-direction: column; height: 100vh; }

.app-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 16px; background: #1a237e; color: white;
}
.app-header h1 { font-size: 16px; font-weight: 600; }
.header-controls { display: flex; align-items: center; gap: 12px; }
.status-on { color: #69f0ae; font-size: 13px; }
.status-warn { color: #ffd54f; font-size: 13px; }
.status-off { color: #ff5252; font-size: 13px; }
.mode-btn {
  padding: 4px 10px; border: 1px solid rgba(255,255,255,0.3);
  border-radius: 4px; background: transparent; color: white;
  cursor: pointer; font-size: 12px;
}
.mode-btn:hover { background: rgba(255,255,255,0.1); }

.app-body { display: flex; flex: 1; overflow: hidden; }

.sidebar-left {
  width: 280px; border-right: 1px solid #e0e0e0;
  background: white; overflow-y: auto;
}
.main-chat {
  flex: 1; display: flex; flex-direction: column;
  background: white;
}
.sidebar-right {
  width: 320px; border-left: 1px solid #e0e0e0;
  background: white; overflow-y: auto;
}

@media (max-width: 900px) {
  .sidebar-left, .sidebar-right { display: none; }
}
</style>

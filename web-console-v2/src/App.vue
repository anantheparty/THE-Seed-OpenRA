<template>
  <div class="app" :class="mode">
    <header class="app-header">
      <h1>THE Seed OpenRA</h1>
      <div class="header-controls">
        <span :class="connected ? 'status-on' : 'status-off'">
          {{ connected ? '● 已连接' : '○ 断开' }}
        </span>
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

      <aside class="sidebar-right">
        <OpsPanel v-if="mode === 'user'" :connected="connected" :send="send" :on="on" @mode-switch="setMode" />
        <DiagPanel v-else :on="on" />
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useWebSocket } from './composables/useWebSocket.js'
import ChatView from './components/ChatView.vue'
import TaskPanel from './components/TaskPanel.vue'
import OpsPanel from './components/OpsPanel.vue'
import DiagPanel from './components/DiagPanel.vue'

const { connected, send, on } = useWebSocket()
const mode = ref('user')

function toggleMode() {
  mode.value = mode.value === 'user' ? 'debug' : 'user'
  send('mode_switch', { mode: mode.value })
}

function setMode(m) {
  mode.value = m
}
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

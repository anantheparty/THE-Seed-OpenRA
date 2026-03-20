<template>
  <div class="ops-panel">
    <h3>Operations</h3>
    <div class="vnc-container">
      <iframe :src="vncUrl" class="vnc-frame" allowfullscreen></iframe>
    </div>

    <h3>Game Control</h3>
    <div class="game-controls">
      <button @click="gameAction('game_start')" class="btn-start">启动游戏</button>
      <button @click="gameAction('game_stop')" class="btn-stop">停止游戏</button>
      <button @click="gameAction('game_restart')" class="btn-restart">重启游戏</button>
    </div>

    <div class="game-status">
      <span :class="gameStale ? 'stale' : 'healthy'">
        {{ gameStale ? '⚠ 数据过期' : '● 数据正常' }}
      </span>
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
import { ref, defineProps, defineEmits } from 'vue'

const props = defineProps({
  connected: Boolean,
  send: Function,
  on: Function,
})
const emit = defineEmits(['mode-switch'])

const vncUrl = ref(
  new URLSearchParams(window.location.search).get('vnc_url') || '/vnc/vnc.html'
)
const gameStale = ref(false)

function switchMode(mode) {
  if (props.send) props.send('mode_switch', { mode })
  emit('mode-switch', mode)
}

function gameAction(action) {
  if (props.send) props.send(action, {})
}

if (props.on) {
  props.on('world_snapshot', (msg) => {
    gameStale.value = !!msg.data?.stale
  })
}
</script>

<style scoped>
.ops-panel { padding: 12px; display: flex; flex-direction: column; height: 100%; }
.ops-panel h3 { margin: 12px 0 6px; font-size: 14px; color: #666; }
.ops-panel h3:first-child { margin-top: 0; }
.vnc-container { flex: 1; min-height: 200px; }
.vnc-frame { width: 100%; height: 100%; border: 1px solid #ddd; border-radius: 4px; }
.game-controls { display: flex; gap: 6px; }
.game-controls button { padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 12px; }
.btn-start { background: #e8f5e9; border-color: #4caf50; }
.btn-start:hover { background: #c8e6c9; }
.btn-stop { background: #ffebee; border-color: #f44336; }
.btn-stop:hover { background: #ffcdd2; }
.btn-restart { background: #fff3e0; border-color: #ff9800; }
.btn-restart:hover { background: #ffe0b2; }
.game-status { margin-top: 6px; font-size: 12px; }
.healthy { color: #4caf50; }
.stale { color: #ff9800; }
.controls { display: flex; gap: 8px; }
.controls button { padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5; cursor: pointer; font-size: 12px; }
.controls button:hover { background: #e0e0e0; }
.connection-status { margin-top: 8px; font-size: 12px; }
.online { color: #4caf50; }
.offline { color: #f44336; }
</style>

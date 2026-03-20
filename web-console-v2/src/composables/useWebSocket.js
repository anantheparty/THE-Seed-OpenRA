import { ref, onUnmounted } from 'vue'

export function useWebSocket(url = 'ws://localhost:8765/ws') {
  const connected = ref(false)
  const messages = ref([])
  let ws = null
  let reconnectTimer = null
  const handlers = {}

  function connect() {
    ws = new WebSocket(url)
    ws.onopen = () => {
      connected.value = true
      // Request full state sync on connect/reconnect
      ws.send(JSON.stringify({ type: 'sync_request', timestamp: Date.now() / 1000 }))
    }
    ws.onclose = () => {
      connected.value = false
      if (!intentionalDisconnect) {
        reconnectTimer = setTimeout(connect, 3000)
      }
    }
    ws.onerror = () => { ws.close() }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        messages.value.push(msg)
        if (msg.type && handlers[msg.type]) {
          handlers[msg.type].forEach(fn => fn(msg))
        }
        if (handlers['*']) {
          handlers['*'].forEach(fn => fn(msg))
        }
      } catch (e) { console.error('WS parse error:', e) }
    }
  }

  function send(type, data = {}) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type, ...data, timestamp: Date.now() / 1000 }))
    }
  }

  function on(type, fn) {
    if (!handlers[type]) handlers[type] = []
    handlers[type].push(fn)
  }

  let intentionalDisconnect = false

  function disconnect() {
    intentionalDisconnect = true
    clearTimeout(reconnectTimer)
    if (ws) ws.close()
  }

  connect()
  onUnmounted(disconnect)

  return { connected, messages, send, on, disconnect }
}

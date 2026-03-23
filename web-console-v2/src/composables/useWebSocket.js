import { ref, onUnmounted } from 'vue'

export function useWebSocket(url = 'ws://localhost:8765/ws') {
  const connected = ref(false)
  const reconnecting = ref(false)
  const messages = ref([])
  let ws = null
  let reconnectTimer = null
  const handlers = {}
  let hasConnectedOnce = false

  function connect() {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
    ws = new WebSocket(url)
    ws.onopen = () => {
      const isReconnect = hasConnectedOnce
      connected.value = true
      reconnecting.value = false
      if (isReconnect) {
        messages.value = []
      }
      hasConnectedOnce = true
      // Request full state sync on connect/reconnect
      ws.send(JSON.stringify({ type: 'sync_request', timestamp: Date.now() / 1000 }))
    }
    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!intentionalDisconnect) {
        reconnecting.value = hasConnectedOnce
        reconnectTimer = setTimeout(connect, 3000)
      } else {
        reconnecting.value = false
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
      return true
    }
    return false
  }

  function on(type, fn) {
    if (!handlers[type]) handlers[type] = []
    handlers[type].push(fn)
    return () => {
      handlers[type] = (handlers[type] || []).filter(item => item !== fn)
      if (handlers[type] && handlers[type].length === 0) {
        delete handlers[type]
      }
    }
  }

  let intentionalDisconnect = false

  function disconnect() {
    intentionalDisconnect = true
    reconnecting.value = false
    clearTimeout(reconnectTimer)
    if (ws) ws.close()
  }

  connect()
  onUnmounted(disconnect)

  return { connected, reconnecting, messages, send, on, disconnect }
}

import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const wsMock = vi.hoisted(() => {
  const send = vi.fn()
  const connected = { value: true }
  const reconnecting = { value: false }
  let handlers = new Map()
  return {
    send,
    connected,
    reconnecting,
    on(type, handler) {
      handlers.set(type, handler)
      return () => handlers.delete(type)
    },
    emit(type, data = {}, timestamp = 123) {
      const handler = handlers.get(type)
      if (handler) handler({ type, data, timestamp })
    },
    reset() {
      send.mockReset()
      connected.value = true
      reconnecting.value = false
      handlers = new Map()
    },
  }
})

vi.mock('../composables/useWebSocket.js', () => ({
  useWebSocket: () => ({
    connected: wsMock.connected,
    reconnecting: wsMock.reconnecting,
    send: wsMock.send,
    on: wsMock.on,
  }),
}))

import App from '../App.vue'

describe('App', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    wsMock.reset()
  })

  it('switches to diagnostics mode and re-emits task focus after a task requests diagnostics', async () => {
    const applied = []
    const handler = (event) => applied.push(event.detail)
    window.addEventListener('theseed:apply-diagnostics-focus', handler)

    try {
      const wrapper = mount(App, {
        global: {
          stubs: {
            ChatView: { template: '<div class="chat-stub" />' },
            TaskPanel: { template: '<div class="task-stub" />' },
            OpsPanel: { template: '<div class="ops-stub" />' },
            DiagPanel: { template: '<div class="diag-stub" />' },
          },
        },
      })

      expect(wrapper.find('.diag-stub').exists()).toBe(false)

      window.dispatchEvent(new CustomEvent('theseed:focus-diagnostics-task', { detail: { taskId: 't_focus' } }))
      await nextTick()
      await nextTick()

      expect(wrapper.find('.diag-stub').exists()).toBe(true)
      expect(applied).toEqual([{ taskId: 't_focus' }])
    } finally {
      window.removeEventListener('theseed:apply-diagnostics-focus', handler)
    }
  })

  it('notifies backend and refreshes diagnostics when external task focus opens debug mode', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          ChatView: { template: '<div class="chat-stub" />' },
          TaskPanel: { template: '<div class="task-stub" />' },
          OpsPanel: { template: '<div class="ops-stub" />' },
        },
      },
    })

    expect(wrapper.find('.diag-panel').exists()).toBe(false)

    window.dispatchEvent(new CustomEvent('theseed:focus-diagnostics-task', { detail: { taskId: 't_focus' } }))
    await nextTick()
    await nextTick()

    expect(wrapper.find('.diag-panel').exists()).toBe(true)
    expect(wsMock.send).toHaveBeenCalledWith('mode_switch', { mode: 'debug' })
    expect(wsMock.send).toHaveBeenCalledWith('diagnostics_sync_request')
  })

  it('requests session_clear first and only clears UI after session_cleared arrives', async () => {
    const clearEvents = []
    const handler = () => clearEvents.push('cleared')
    window.addEventListener('theseed:clear-ui', handler)

    try {
      const wrapper = mount(App, {
        global: {
          stubs: {
            ChatView: { template: '<div class="chat-stub" />' },
            TaskPanel: { template: '<div class="task-stub" />' },
            OpsPanel: { template: '<div class="ops-stub" />' },
            DiagPanel: { template: '<div class="diag-stub" />' },
          },
        },
      })

      const clearButton = wrapper.findAll('button').find((button) => button.text() === '清空界面')
      expect(clearButton).toBeTruthy()

      await clearButton.trigger('click')

      expect(wsMock.send).toHaveBeenCalledWith('session_clear')
      expect(clearEvents).toEqual([])

      wsMock.emit('session_cleared', { ok: true })
      await nextTick()

      expect(clearEvents).toEqual(['cleared'])
    } finally {
      window.removeEventListener('theseed:clear-ui', handler)
    }
  })
})

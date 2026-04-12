import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../composables/useWebSocket.js', () => ({
  useWebSocket: () => ({
    connected: { value: true },
    reconnecting: { value: false },
    send: vi.fn(),
    on: vi.fn(() => () => {}),
  }),
}))

import App from '../App.vue'

describe('App', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
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
})

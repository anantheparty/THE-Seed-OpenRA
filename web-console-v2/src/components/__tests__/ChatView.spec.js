import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatView from '../ChatView.vue'

function createBus() {
  const handlers = new Map()
  return {
    on(type, handler) {
      const list = handlers.get(type) || []
      list.push(handler)
      handlers.set(type, list)
      return () => {
        const next = (handlers.get(type) || []).filter(item => item !== handler)
        if (next.length === 0) handlers.delete(type)
        else handlers.set(type, next)
      }
    },
    emit(type, data, timestamp = 123) {
      for (const handler of handlers.get(type) || []) {
        handler({ data, timestamp, type })
      }
    },
    count(type) {
      return (handlers.get(type) || []).length
    },
  }
}

describe('ChatView', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.__ttsOn = false
    vi.restoreAllMocks()
  })

  it('sends question_reply from task-question options and disables them after answering', async () => {
    const bus = createBus()
    const send = vi.fn(() => true)

    const wrapper = mount(ChatView, {
      props: {
        connected: true,
        send,
        on: bus.on,
      },
    })

    bus.emit('task_message', {
      type: 'task_question',
      task_id: 't_ask',
      message_id: 'msg_ask',
      content: '继续推进还是等待？',
      options: ['继续', '等待'],
    })
    await wrapper.vm.$nextTick()

    const buttons = wrapper.findAll('.option-btn')
    expect(buttons).toHaveLength(3)
    expect(buttons.map((item) => item.text())).toEqual(['继续', '等待', '取消任务'])

    await buttons[0].trigger('click')

    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('question_reply', {
      message_id: 'msg_ask',
      task_id: 't_ask',
      answer: '继续',
    })

    const updatedButtons = wrapper.findAll('.option-btn')
    expect(updatedButtons[0].attributes('disabled')).toBeDefined()
    expect(updatedButtons[1].attributes('disabled')).toBeDefined()
    expect(updatedButtons[2].attributes('disabled')).toBeDefined()

    await updatedButtons[0].trigger('click')
    expect(send).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('offers command_cancel from task-question messages', async () => {
    const bus = createBus()
    const send = vi.fn(() => true)

    const wrapper = mount(ChatView, {
      props: {
        connected: true,
        send,
        on: bus.on,
      },
    })

    bus.emit('task_message', {
      type: 'task_question',
      task_id: 't_cancel_me',
      message_id: 'msg_cancel_me',
      content: '任务"V"已启动，但目标不明确。请指定您希望执行的具体行动：',
      options: ['攻击', '侦察'],
    })
    await wrapper.vm.$nextTick()

    const buttons = wrapper.findAll('.option-btn')
    expect(buttons.map((item) => item.text())).toEqual(['攻击', '侦察', '取消任务'])

    await buttons[2].trigger('click')

    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('command_cancel', {
      task_id: 't_cancel_me',
    })

    const updatedButtons = wrapper.findAll('.option-btn')
    expect(updatedButtons[0].attributes('disabled')).toBeDefined()
    expect(updatedButtons[1].attributes('disabled')).toBeDefined()
    expect(updatedButtons[2].attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('clears chat history on theseed:clear-ui and unregisters websocket handlers on unmount', async () => {
    const bus = createBus()
    const wrapper = mount(ChatView, {
      props: {
        connected: true,
        send: () => true,
        on: bus.on,
      },
    })

    expect(bus.count('query_response')).toBe(1)
    expect(bus.count('player_notification')).toBe(1)
    expect(bus.count('task_message')).toBe(1)

    bus.emit('query_response', {
      response_type: 'command',
      answer: '收到指令，已创建任务 t_demo',
      task_id: 't_demo',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.chat-msg')).toHaveLength(1)
    expect(JSON.parse(window.sessionStorage.getItem('theseed_chat_history_session'))).toHaveLength(1)

    window.dispatchEvent(new CustomEvent('theseed:clear-ui'))
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.chat-msg')).toHaveLength(0)
    expect(window.sessionStorage.getItem('theseed_chat_history_session')).toBeNull()

    wrapper.unmount()
    expect(bus.count('query_response')).toBe(0)
    expect(bus.count('player_notification')).toBe(0)
    expect(bus.count('task_message')).toBe(0)

    bus.emit('query_response', {
      response_type: 'command',
      answer: '这条消息不应再出现',
      task_id: 't_demo',
    })
    expect(window.sessionStorage.getItem('theseed_chat_history_session')).toBeNull()
  })

  it('renders sent player commands as player-side chat bubbles', async () => {
    const bus = createBus()
    const send = vi.fn(() => true)
    const wrapper = mount(ChatView, {
      props: {
        connected: true,
        send,
        on: bus.on,
      },
    })

    await wrapper.find('input').setValue('发展一下科技')
    await wrapper.find('button:last-of-type').trigger('click')
    await wrapper.vm.$nextTick()

    expect(send).toHaveBeenCalledWith('command_submit', { text: '发展一下科技' })
    const playerMsg = wrapper.find('.chat-msg.player')
    expect(playerMsg.exists()).toBe(true)
    expect(playerMsg.find('.msg-label').text()).toBe('玩家')
    expect(playerMsg.find('.msg-content').text()).toBe('发展一下科技')
  })

  it('auto-sends recognized ASR text after recording stops', async () => {
    const bus = createBus()
    const send = vi.fn(() => true)
    const trackStop = vi.fn()
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: trackStop }],
    })
    Object.defineProperty(globalThis.navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })

    class FakeMediaRecorder {
      static isTypeSupported(type) {
        return type === 'audio/webm;codecs=opus'
      }

      constructor(stream, options) {
        this.stream = stream
        this.options = options
        this.state = 'inactive'
        this.ondataavailable = null
        this.onstop = null
        this.onerror = null
      }

      start() {
        this.state = 'recording'
      }

      requestData() {
        if (this.ondataavailable) {
          this.ondataavailable({
            data: new Blob([new Uint8Array(1024)], { type: 'audio/webm;codecs=opus' }),
          })
        }
      }

      stop() {
        this.requestData()
        this.state = 'inactive'
        if (this.onstop) {
          this.onstop()
        }
      }
    }

    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, text: '建造电厂' }),
    }))
    let now = 1000
    vi.spyOn(Date, 'now').mockImplementation(() => now)

    const wrapper = mount(ChatView, {
      props: {
        connected: true,
        send,
        on: bus.on,
      },
    })

    await wrapper.find('button.mic-btn').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    now = 2000
    await wrapper.find('button.mic-btn').trigger('click')
    await wrapper.vm.$nextTick()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(trackStop).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('command_submit', { text: '建造电厂' })
    const playerMsg = wrapper.find('.chat-msg.player')
    expect(playerMsg.exists()).toBe(true)
    expect(playerMsg.find('.msg-content').text()).toBe('建造电厂')

    wrapper.unmount()
  })
})

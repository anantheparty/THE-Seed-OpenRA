import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import OpsPanel from '../OpsPanel.vue'

function createBus() {
  const handlers = new Map()
  return {
    on(type, handler) {
      handlers.set(type, handler)
    },
    emit(type, data) {
      const handler = handlers.get(type)
      if (handler) handler({ data })
    },
  }
}

describe('OpsPanel', () => {
  it('only exposes restart control and emits game_restart', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send,
        on: bus.on,
      },
    })

    const buttonTexts = wrapper.findAll('button').map((button) => button.text())
    expect(buttonTexts).toContain('重启游戏')
    expect(buttonTexts).not.toContain('启动游戏')
    expect(buttonTexts).not.toContain('停止游戏')

    await wrapper.get('.btn-restart').trigger('click')
    expect(send).toHaveBeenCalledWith('game_restart', {})
  })

  it('renders stale world details from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: false,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      stale: true,
      consecutive_refresh_failures: 4,
      failure_threshold: 3,
      last_refresh_error: 'actors:COMMAND_EXECUTION_ERROR',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('⚠ 数据过期 (4/3)')
    expect(wrapper.text()).toContain('连续失败 4 / 3')
    expect(wrapper.text()).toContain('最近错误: actors:COMMAND_EXECUTION_ERROR')
    expect(wrapper.text()).toContain('WS 断开')
  })

  it('renders capability truth blocker from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      player_faction: 'allied',
      capability_truth_blocker: 'faction_roster_unsupported',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('能力真值受限')
    expect(wrapper.text()).toContain('demo capability roster 未覆盖 (allied)')
    expect(wrapper.text()).toContain('阵营: allied')
  })

  it('renders unit pipeline preview from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      unit_pipeline_preview: '步兵 × 1 · 待分发',
      unit_pipeline_focus: {
        detail: '步兵 × 1 <- 待分发',
        task_id: 't_recon',
        task_label: '002',
        request_count: 1,
        reservation_count: 1,
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('能力在途: 步兵 × 1 · 待分发')
    expect(wrapper.text()).toContain('请求 1 · 预留 1')
    expect(wrapper.text()).toContain('当前卡点: #002 · 步兵 × 1 <- 待分发')
  })

  it('dispatches diagnostics focus for the capability task from ops status actions', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      capability_truth_blocker: 'queue_blocked',
      unit_pipeline_preview: '步兵 × 1 · 待分发',
      runtime_state: {
        capability_status: {
          task_id: 't_cap',
        },
      },
    })
    await wrapper.vm.$nextTick()

    const events = []
    const handler = (event) => events.push(event.detail)
    window.addEventListener('theseed:focus-diagnostics-task', handler)
    try {
      const buttons = wrapper.findAll('.diag-link-btn')
      expect(buttons).toHaveLength(2)
      await buttons[0].trigger('click')
      await buttons[1].trigger('click')
    } finally {
      window.removeEventListener('theseed:focus-diagnostics-task', handler)
    }

    expect(events).toEqual([{ taskId: 't_cap' }, { taskId: 't_cap' }])
  })
})

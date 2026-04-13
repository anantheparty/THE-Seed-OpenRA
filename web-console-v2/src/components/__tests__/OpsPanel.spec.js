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

  it('renders disconnect state distinctly from generic stale world status', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      stale: true,
      disconnected: true,
      consecutive_refresh_failures: 2,
      failure_threshold: 3,
      last_refresh_error: 'actors:CONNECTION_ERROR: connection refused',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('⚠ 游戏连接断开 (2/3)')
    expect(wrapper.text()).toContain('连接状态: 已断开')
    expect(wrapper.text()).toContain('最近错误: actors:CONNECTION_ERROR: connection refused')
  })

  it('renders runtime fault detail from world_snapshot when not stale', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      runtime_fault_state: {
        degraded: true,
        source: 'dashboard_publish',
        stage: 'task_messages',
        error: "RuntimeError('publish-boom')",
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('⚠ 运行降级')
    expect(wrapper.text()).toContain('运行时降级: dashboard_publish / task_messages')
    expect(wrapper.text()).toContain("错误: RuntimeError('publish-boom')")
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
        reservation_status: 'pending',
        remaining_count: 1,
        assigned_count: 0,
        produced_count: 0,
        start_released: false,
        bootstrap_job_id: 'j_boot',
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('能力在途: 步兵 × 1 · 待分发')
    expect(wrapper.text()).toContain('请求 1 · 预留 1')
    expect(wrapper.text()).toContain('状态 pending · 剩余 1 · bootstrap=j_boot')
    expect(wrapper.text()).toContain('当前卡点: #002 · 步兵 × 1 <- 待分发')
  })

  it('renders compact secondary unit pipeline previews without duplicating the focus item', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      unit_pipeline_preview: '重坦 × 1 · 低电',
      unit_pipeline_focus: {
        detail: '重坦 × 1 <- 当前低电',
        task_id: 't_power',
        task_label: '003',
        request_count: 4,
        reservation_count: 4,
        reservation_status: 'pending',
        remaining_count: 1,
      },
      unit_pipeline_preview_items: [
        {
          preview: '重坦 × 1 · 低电',
          task_id: 't_power',
          task_label: '003',
          reason: 'low_power',
          reason_text: '低电',
          reservation_status: 'pending',
        },
        {
          preview: 'v2 × 1 · 前置生产中',
          task_id: 't_boot',
          task_label: '004',
          reason: 'bootstrap_in_progress',
          reason_text: '前置生产中',
          reservation_status: 'pending',
        },
        {
          preview: '步兵 × 1 · 待分发',
          task_id: 't_dispatch',
          task_label: '002',
          reason: 'waiting_dispatch',
          reason_text: '待分发',
          reservation_status: 'pending',
        },
      ],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('能力在途: 重坦 × 1 · 低电')
    expect(wrapper.text()).toContain('其他在途: #004 v2 × 1 · 前置生产中；#002 步兵 × 1 · 待分发')
    expect(wrapper.text()).not.toContain('其他在途: #003 重坦 × 1 · 低电')
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
      unit_pipeline_focus: {
        task_id: 't_req',
        task_label: '002',
        detail: '步兵 × 1 <- 待分发',
        request_count: 1,
        reservation_count: 1,
        reservation_status: 'pending',
        remaining_count: 1,
      },
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
      expect(buttons).toHaveLength(3)
      await buttons[0].trigger('click')
      await buttons[1].trigger('click')
      await buttons[2].trigger('click')
    } finally {
      window.removeEventListener('theseed:focus-diagnostics-task', handler)
    }

    expect(events).toEqual([{ taskId: 't_cap' }, { taskId: 't_cap' }, { taskId: 't_req' }])
  })

  it('aggregates stale, runtime fault, capability truth, and pipeline blockage in the primary status', async () => {
    const bus = createBus()
    const wrapper = mount(OpsPanel, {
      props: {
        connected: true,
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      stale: true,
      consecutive_refresh_failures: 4,
      failure_threshold: 3,
      runtime_fault_state: {
        degraded: true,
        source: 'dashboard_publish',
        stage: 'task_messages',
        error: "RuntimeError('publish-boom')",
      },
      player_faction: 'allied',
      capability_truth_blocker: 'faction_roster_unsupported',
      unit_pipeline_preview: '步兵 × 1 · 待分发',
      unit_pipeline_focus: {
        detail: '步兵 × 1 <- 待分发',
        task_id: 't_req',
        task_label: '002',
        request_count: 1,
        reservation_count: 1,
        reservation_status: 'pending',
        remaining_count: 1,
      },
      runtime_state: {
        capability_status: {
          task_id: 't_cap',
        },
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('⚠ 数据过期 (4/3) · 运行降级 · 能力受限 · 管线阻塞')
    expect(wrapper.text()).toContain('运行时降级: dashboard_publish / task_messages')
    expect(wrapper.text()).toContain('能力真值受限: demo capability roster 未覆盖 (allied)')
    expect(wrapper.text()).toContain('当前卡点: #002 · 步兵 × 1 <- 待分发')
  })
})

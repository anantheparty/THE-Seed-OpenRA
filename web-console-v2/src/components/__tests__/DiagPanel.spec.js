import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DiagPanel from '../DiagPanel.vue'

function createBus() {
  const handlers = new Map()
  return {
    on(type, handler) {
      handlers.set(type, handler)
      return () => handlers.delete(type)
    },
    emit(type, data, timestamp = 123) {
      const handler = handlers.get(type)
      if (handler) handler({ data, timestamp })
    },
  }
}

describe('DiagPanel', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('renders structured triage blocker and reservation fields for the selected task', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(DiagPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_cap',
          raw_text: '发展科技',
          status: 'running',
          timestamp: 100,
          created_at: 90,
          triage: {
            status_line: '能力处理中：分发请求中 | blocker=缺少前置建筑 (1)',
            state: 'running',
            phase: 'dispatch',
            waiting_reason: 'missing_prerequisite',
            blocking_reason: 'missing_prerequisite',
            reservation_ids: ['res_1', 'res_2'],
            reservation_preview: '重坦 × 2 · 缺少前置',
            world_stale: true,
            world_sync_failures: 2,
            world_sync_failure_threshold: 3,
            world_sync_error: 'actors:COMMAND_EXECUTION_ERROR',
          },
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_cap')
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('能力处理中：分发请求中 | blocker=缺少前置建筑 (1)')
    expect(wrapper.text()).toContain('waiting=missing_prerequisite')
    expect(wrapper.text()).toContain('blocker=missing_prerequisite')
    expect(wrapper.text()).toContain('reservations=2')
    expect(wrapper.text()).toContain('reservation=重坦 × 2 · 缺少前置')
    expect(wrapper.text()).toContain('world=stale')
    expect(wrapper.text()).toContain('sync_fail=2/3')
    expect(wrapper.text()).toContain('sync=actors:COMMAND_EXECUTION_ERROR')
  })

  it('renders structured triage fields inside the replay current-runtime summary', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(DiagPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_cap',
          raw_text: '发展科技',
          status: 'running',
          timestamp: 100,
          created_at: 90,
          triage: {
            status_line: '能力处理中',
            state: 'running',
          },
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_cap')
    await wrapper.vm.$nextTick()

    bus.emit('task_replay', {
      task_id: 't_cap',
      bundle: {
        summary: '回放摘要',
        entry_count: 3,
        duration_s: 12.5,
        current_runtime: {
          triage: {
            status_line: '等待能力模块交付单位：重坦 × 2',
            state: 'waiting_units',
            phase: 'reservation',
            waiting_reason: 'unit_reservation',
            blocking_reason: 'missing_prerequisite',
            reservation_ids: ['res_1'],
            reservation_preview: '重坦 × 2 · 缺少前置',
            active_expert: 'EconomyExpert',
            world_sync_failures: 4,
            world_sync_failure_threshold: 3,
            world_sync_error: 'economy disconnected',
          },
        },
      },
      raw_entry_count: 0,
      entry_count: 3,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Current Runtime')
    expect(wrapper.text()).toContain('等待能力模块交付单位：重坦 × 2')
    expect(wrapper.text()).toContain('waiting=unit_reservation')
    expect(wrapper.text()).toContain('blocker=missing_prerequisite')
    expect(wrapper.text()).toContain('reservations=1')
    expect(wrapper.text()).toContain('reservation=重坦 × 2 · 缺少前置')
    expect(wrapper.text()).toContain('expert=EconomyExpert')
    expect(wrapper.text()).toContain('sync_fail=4/3')
    expect(wrapper.text()).toContain('sync=economy disconnected')
  })

  it('renders compact capability truth inside replay diagnostics', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(DiagPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_cap',
          raw_text: '发展科技',
          status: 'running',
          timestamp: 100,
          created_at: 90,
          triage: {
            status_line: '能力处理中',
            state: 'running',
          },
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_cap')
    await wrapper.vm.$nextTick()

    bus.emit('task_replay', {
      task_id: 't_cap',
      bundle: {
        summary: '回放摘要',
        entry_count: 3,
        duration_s: 12.5,
        current_runtime: {
          triage: {
            status_line: '能力处理中：待机',
            state: 'idle',
          },
        },
        capability_truth: {
          truth_blocker: '',
          faction: 'soviet',
          base_status: '下一步：矿场',
          next_unit_type: 'proc',
          blocking_reason: 'low_power',
          buildable_now: false,
          issue_now: ['Building:powr'],
          blocked_now: ['Building:proc:low_power'],
          ready_items: ['Building:发电厂'],
        },
      },
      raw_entry_count: 0,
      entry_count: 3,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Capability Truth')
    expect(wrapper.text()).toContain('faction=soviet')
    expect(wrapper.text()).toContain('base=下一步：矿场')
    expect(wrapper.text()).toContain('next=proc')
    expect(wrapper.text()).toContain('block_reason=low_power')
    expect(wrapper.text()).toContain('issue=Building:powr')
    expect(wrapper.text()).toContain('blocked=Building:proc:low_power')
    expect(wrapper.text()).toContain('ready=Building:发电厂')
  })

  it('renders replay_triage when current runtime triage is unavailable', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(DiagPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/session-1',
          session_name: 'session-1',
          task_count: 1,
          record_count: 10,
        },
      ],
      current_session_dir: '/live/session',
    })
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/session-1',
      tasks: [
        {
          task_id: 't_hist',
          raw_text: '历史任务',
          status: 'partial',
          timestamp: 100,
          created_at: 90,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#session-select').setValue('/tmp/session-1')
    await wrapper.vm.$nextTick()
    await wrapper.find('#task-trace-select').setValue('t_hist')
    await wrapper.vm.$nextTick()

    bus.emit('task_replay', {
      task_id: 't_hist',
      session_dir: '/tmp/session-1',
      bundle: {
        summary: '历史阻塞：猛犸坦克 × 1 缺少前置',
        entry_count: 5,
        duration_s: 18.0,
        replay_triage: {
          status_line: '历史阻塞：猛犸坦克 × 1 缺少前置',
          state: 'blocked',
          phase: 'blocked',
          waiting_reason: 'missing_prerequisite',
          blocking_reason: 'missing_prerequisite',
          reservation_ids: ['res_1'],
          reservation_preview: '猛犸坦克 × 1 · 缺少前置',
        },
      },
      raw_entry_count: 0,
      entry_count: 5,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Replay Triage')
    expect(wrapper.text()).toContain('历史阻塞：猛犸坦克 × 1 缺少前置')
    expect(wrapper.text()).toContain('state=blocked')
    expect(wrapper.text()).toContain('phase=blocked')
    expect(wrapper.text()).toContain('waiting=missing_prerequisite')
    expect(wrapper.text()).toContain('blocker=missing_prerequisite')
    expect(wrapper.text()).toContain('reservations=1')
    expect(wrapper.text()).toContain('reservation=猛犸坦克 × 1 · 缺少前置')
  })

  it('renders world-sync stale details from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      stale: true,
      consecutive_refresh_failures: 5,
      failure_threshold: 3,
      last_refresh_error: 'actors:COMMAND_EXECUTION_ERROR',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('世界状态同步异常')
    expect(wrapper.text()).toContain('world=stale')
    expect(wrapper.text()).toContain('failures=5/3')
    expect(wrapper.text()).toContain('threshold=3')
    expect(wrapper.text()).toContain('error=actors:COMMAND_EXECUTION_ERROR')
  })

  it('renders capability truth blocker from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
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
    expect(wrapper.text()).toContain('blocker=faction_roster_unsupported')
    expect(wrapper.text()).toContain('faction=allied')
    expect(wrapper.text()).toContain('demo capability roster 未覆盖当前阵营')
  })

  it('refreshes selected live replay when world truth changes', async () => {
    vi.useFakeTimers()
    try {
      const bus = createBus()
      const send = vi.fn(() => true)
      const wrapper = mount(DiagPanel, {
        props: {
          send,
          on: bus.on,
        },
      })

      bus.emit('task_list', {
        tasks: [
          {
            task_id: 't_cap',
            raw_text: '发展科技',
            status: 'running',
            timestamp: 100,
            created_at: 90,
            triage: {
              status_line: '能力处理中',
              state: 'running',
            },
          },
        ],
      })
      await wrapper.vm.$nextTick()
      await wrapper.find('#task-trace-select').setValue('t_cap')
      await wrapper.vm.$nextTick()
      send.mockClear()

      bus.emit('world_snapshot', {
        stale: true,
        consecutive_refresh_failures: 3,
        failure_threshold: 3,
        last_refresh_error: 'actors:COMMAND_EXECUTION_ERROR',
      })
      await wrapper.vm.$nextTick()
      await vi.advanceTimersByTimeAsync(1000)

      expect(send).toHaveBeenCalledWith('task_replay_request', {
        task_id: 't_cap',
        session_dir: null,
        include_entries: false,
      })
    } finally {
      vi.useRealTimers()
    }
  })
})

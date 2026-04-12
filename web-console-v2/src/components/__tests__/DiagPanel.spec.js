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

  it('renders reservation lifecycle replay highlights with compact transition details', async () => {
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
          task_id: 't_res',
          raw_text: '装甲推进',
          status: 'running',
          timestamp: 100,
          created_at: 90,
          triage: {
            status_line: '等待能力模块交付单位：重坦 × 3',
            state: 'waiting_units',
          },
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_res')
    await wrapper.vm.$nextTick()

    bus.emit('task_replay', {
      task_id: 't_res',
      bundle: {
        summary: '回放摘要',
        entry_count: 4,
        duration_s: 6.2,
        highlights: [
          {
            label: 'unit_request_fulfilled',
            message: 'Unit request fulfilled from idle',
            data: {
              request_id: 'req_idle',
              reservation_id: 'res_idle',
              assigned_count: 2,
              produced_count: 0,
            },
          },
          {
            label: 'unit_request_start_released',
            message: 'Unit request start released',
            data: {
              request_id: 'req_release',
              reservation_id: 'res_release',
              status: 'partial',
              assigned_count: 2,
              produced_count: 1,
              remaining_count: 1,
            },
          },
          {
            label: 'unit_request_cancelled',
            message: 'Unit request cancelled',
            data: {
              request_id: 'req_cancel',
              reservation_id: 'res_cancel',
              remaining_count: 2,
            },
          },
        ],
      },
      raw_entry_count: 0,
      entry_count: 4,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('[unit_request_fulfilled] Unit request fulfilled from idle · req=req_idle · res=res_idle · assigned=2')
    expect(wrapper.text()).toContain('[unit_request_start_released] Unit request start released · req=req_release · res=res_release · status=partial · assigned=2 · produced=1 · remaining=1')
    expect(wrapper.text()).toContain('[unit_request_cancelled] Unit request cancelled · req=req_cancel · res=res_cancel · remaining=2')
  })

  it('renders compact live runtime summary from world_snapshot runtime_state', async () => {
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
          raw_text: '发展经济',
          status: 'running',
          timestamp: 100,
          created_at: 90,
        },
        {
          task_id: 't_move',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 110,
          created_at: 95,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_move')
    await wrapper.vm.$nextTick()

    bus.emit('world_snapshot', {
      runtime_state: {
        active_tasks: {
          t_cap: {
            label: '001',
            is_capability: true,
            active_group_size: 0,
            active_actor_ids: [],
          },
          t_move: {
            label: '002',
            active_group_size: 2,
            active_actor_ids: [101, 102],
          },
        },
        active_jobs: {
          j_move: {
            task_id: 't_move',
            expert_type: 'MovementExpert',
            status: 'running',
          },
        },
        unfulfilled_requests: [{ request_id: 'req_1' }],
        unit_reservations: [{ reservation_id: 'res_1' }],
        capability_status: {
          task_id: 't_cap',
          task_label: '001',
          phase: 'dispatch',
          blocker: 'missing_prerequisite',
        },
      },
      unit_pipeline_preview: '步兵 × 1 · 待分发',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Live Runtime')
    expect(wrapper.text()).toContain('cap=001')
    expect(wrapper.text()).toContain('cap_phase=dispatch')
    expect(wrapper.text()).toContain('cap_blocker=missing_prerequisite')
    expect(wrapper.text()).toContain('tasks=2')
    expect(wrapper.text()).toContain('jobs=1')
    expect(wrapper.text()).toContain('req=1')
    expect(wrapper.text()).toContain('res=1')
    expect(wrapper.text()).toContain('selected=002')
    expect(wrapper.text()).toContain('group=2')
    expect(wrapper.text()).toContain('actors=101,102')
    expect(wrapper.text()).toContain('步兵 × 1 · 待分发')
  })

  it('applies external diagnostics focus events to the selected task', async () => {
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
          session_dir: '/tmp/live-session',
          session_name: 'live-session',
          is_current: true,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_one',
          raw_text: '建造电厂',
          status: 'running',
          timestamp: 100,
        },
        {
          task_id: 't_two',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 110,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    window.dispatchEvent(new CustomEvent('theseed:apply-diagnostics-focus', { detail: { taskId: 't_two' } }))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('#task-trace-select').element.value).toBe('t_two')
    expect(send.mock.calls.some(([type, payload]) => (
      type === 'task_replay_request'
      && payload.task_id === 't_two'
      && payload.session_dir === '/tmp/live-session'
    ))).toBe(true)
  })

  it('renders richer task selector labels from live triage and historical summaries', async () => {
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
          session_dir: '/tmp/live-session',
          session_name: 'live-session',
          is_current: true,
        },
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_live',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 100,
          triage: {
            status_line: '等待能力模块恢复电力：发电厂 × 1',
            active_expert: 'EconomyExpert',
          },
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    const liveOptions = wrapper.findAll('#task-trace-select option').map((item) => item.text())
    expect(liveOptions.some((text) => text.includes('推进前线') && text.includes('等待能力模块恢复电力'))).toBe(true)

    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_hist',
          raw_text: '历史任务',
          status: 'partial',
          summary: '历史阻塞：猛犸坦克 × 1 缺少前置',
          timestamp: 80,
        },
      ],
    })
    await wrapper.find('#session-select').setValue('/tmp/history-session')
    await wrapper.vm.$nextTick()

    const historyOptions = wrapper.findAll('#task-trace-select option').map((item) => item.text())
    expect(historyOptions.some((text) => text.includes('历史任务') && text.includes('历史阻塞：猛犸坦克 × 1 缺少前置'))).toBe(true)
  })

  it('falls back to status text for historical tasks without persisted summaries', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
          is_current: true,
        },
      ],
      selected_session_dir: '/tmp/history-session',
    })
    await wrapper.vm.$nextTick()
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_hist_status',
          raw_text: '历史任务',
          status: 'partial',
          timestamp: 80,
        },
      ],
    })
    await wrapper.find('#session-select').setValue('/tmp/history-session')
    await wrapper.vm.$nextTick()

    const options = wrapper.findAll('#task-trace-select option').map((item) => item.text())
    expect(options.some((text) => text.includes('历史任务') && text.includes('status=partial'))).toBe(true)
  })

  it('prefers live triage over persisted summary when current session merges catalogs', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/live-session',
          session_name: 'live-session',
          is_current: true,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/live-session',
      tasks: [
        {
          task_id: 't_same',
          raw_text: '推进前线',
          status: 'running',
          summary: '历史摘要：旧阻塞',
          timestamp: 90,
        },
      ],
    })
    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_same',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 100,
          triage: {
            status_line: '实时状态：等待能力模块恢复电力',
          },
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    const options = wrapper.findAll('#task-trace-select option').map((item) => item.text())
    expect(options.some((text) => text.includes('推进前线') && text.includes('实时状态：等待能力模块恢复电力'))).toBe(true)
    expect(options.some((text) => text.includes('历史摘要：旧阻塞'))).toBe(false)
  })

  it('shows a compact selected-task catalog summary before replay details load', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
          is_current: true,
        },
      ],
      selected_session_dir: '/tmp/history-session',
    })
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_hist_meta',
          raw_text: '历史任务',
          status: 'partial',
          summary: '历史阻塞：猛犸坦克 × 1 缺少前置',
          entry_count: 7,
          timestamp: 80,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_hist_meta')
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('status=partial')
    expect(wrapper.text()).toContain('entries=7')
    expect(wrapper.text()).toContain('summary=历史阻塞：猛犸坦克 × 1 缺少前置')
  })

  it('hides the selected-task catalog strip again when switching back to ALL', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
          is_current: true,
        },
      ],
      selected_session_dir: '/tmp/history-session',
    })
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_hist_meta',
          raw_text: '历史任务',
          status: 'partial',
          summary: '历史阻塞：猛犸坦克 × 1 缺少前置',
          entry_count: 7,
          timestamp: 80,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_hist_meta')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.selected-task-meta').exists()).toBe(true)

    await wrapper.find('#task-trace-select').setValue('ALL')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.selected-task-meta').exists()).toBe(false)
  })

  it('resets selected task to ALL after switching sessions and receiving a catalog without that task', async () => {
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
          session_dir: '/tmp/live-session',
          session_name: 'live-session',
          is_current: true,
        },
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
        },
      ],
      selected_session_dir: '/tmp/live-session',
      current_session_dir: '/tmp/live-session',
    })
    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_live',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 100,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_live')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.selected-task-meta').exists()).toBe(true)

    await wrapper.find('#session-select').setValue('/tmp/history-session')
    await wrapper.vm.$nextTick()

    expect(send).toHaveBeenCalledWith('session_select', { session_dir: '/tmp/history-session' })

    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_hist',
          raw_text: '历史任务',
          status: 'partial',
          timestamp: 80,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('#task-trace-select').element.value).toBe('ALL')
    expect(wrapper.find('.selected-task-meta').exists()).toBe(false)
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

  it('prefers current runtime triage over replay_triage when both are present', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
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
        entry_count: 5,
        duration_s: 9.0,
        current_runtime: {
          triage: {
            status_line: '实时状态：等待能力任务交付',
            state: 'waiting_units',
            phase: 'reservation',
            waiting_reason: 'unit_reservation',
            reservation_ids: ['res_live'],
          },
        },
        replay_triage: {
          status_line: '历史状态：缺少前置建筑',
          state: 'blocked',
          phase: 'blocked',
          waiting_reason: 'missing_prerequisite',
          reservation_ids: ['res_hist'],
        },
      },
      raw_entry_count: 0,
      entry_count: 5,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Current Runtime')
    expect(wrapper.text()).toContain('实时状态：等待能力任务交付')
    expect(wrapper.text()).toContain('state=waiting_units')
    expect(wrapper.text()).toContain('phase=reservation')
    expect(wrapper.text()).toContain('waiting=unit_reservation')
    expect(wrapper.text()).toContain('reservations=1')
    expect(wrapper.text()).not.toContain('Replay Triage')
    expect(wrapper.text()).not.toContain('历史状态：缺少前置建筑')
    expect(wrapper.text()).not.toContain('state=blocked')
  })

  it('renders current runtime triage when replay_triage is absent', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
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
        entry_count: 4,
        duration_s: 8.0,
        current_runtime: {
          triage: {
            status_line: '实时状态：编队已就绪',
            state: 'running',
            phase: 'active',
            active_expert: 'CombatExpert',
          },
        },
      },
      raw_entry_count: 0,
      entry_count: 4,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Current Runtime')
    expect(wrapper.text()).toContain('实时状态：编队已就绪')
    expect(wrapper.text()).toContain('state=running')
    expect(wrapper.text()).toContain('phase=active')
    expect(wrapper.text()).toContain('expert=CombatExpert')
    expect(wrapper.text()).not.toContain('Replay Triage')
  })

  it('falls back to replay_triage when current runtime exists but triage is null', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
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
        entry_count: 4,
        duration_s: 8.0,
        current_runtime: {
          triage: null,
        },
        replay_triage: {
          status_line: '历史状态：等待前置建筑',
          state: 'blocked',
          phase: 'blocked',
          waiting_reason: 'missing_prerequisite',
        },
      },
      raw_entry_count: 0,
      entry_count: 4,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Replay Triage')
    expect(wrapper.text()).toContain('历史状态：等待前置建筑')
    expect(wrapper.text()).toContain('state=blocked')
    expect(wrapper.text()).toContain('phase=blocked')
    expect(wrapper.text()).toContain('waiting=missing_prerequisite')
    expect(wrapper.text()).not.toContain('Current Runtime')
  })

  it('renders selected session world health summary from session_catalog', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/session-health',
          session_name: 'session-health',
          task_count: 1,
          record_count: 12,
          is_current: true,
          task_rollup: {
            total: 4,
            non_terminal: 1,
            terminal: 3,
            by_status: {
              running: 1,
              succeeded: 2,
              partial: 1,
            },
          },
          world_health: {
            stale_seen: true,
            ended_stale: false,
            stale_refreshes: 9,
            max_consecutive_failures: 4,
            failure_threshold: 3,
            slow_events: 2,
            max_total_ms: 154.2,
            last_failure_layer: 'actors',
            last_error: 'actors:COMMAND_EXECUTION_ERROR',
            last_error_detail: 'Attempted to get trait from destroyed object',
          },
        },
      ],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Session 曾出现世界同步异常')
    expect(wrapper.text()).toContain('task_summary')
    expect(wrapper.text()).toContain('non_terminal=1')
    expect(wrapper.text()).toContain('terminal=3')
    expect(wrapper.text()).toContain('running=1')
    expect(wrapper.text()).toContain('succeeded=2')
    expect(wrapper.text()).toContain('partial=1')
    expect(wrapper.text()).toContain('sync_fail=max 4/3')
    expect(wrapper.text()).toContain('stale_refresh=9')
    expect(wrapper.text()).toContain('slow=2')
    expect(wrapper.text()).toContain('max_refresh=154.2ms')
    expect(wrapper.text()).toContain('layer=actors')
    expect(wrapper.text()).toContain('last=actors:COMMAND_EXECUTION_ERROR')
    expect(wrapper.text()).toContain('detail=Attempted to get trait from destroyed object')
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

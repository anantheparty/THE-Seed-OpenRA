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
            reservation_status: 'pending',
            remaining_count: 2,
            assigned_count: 0,
            produced_count: 0,
            start_released: false,
            bootstrap_job_id: 'j_boot',
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
    expect(wrapper.text()).toContain('res_status=pending')
    expect(wrapper.text()).toContain('remaining=2')
    expect(wrapper.text()).toContain('bootstrap=j_boot')
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
            reservation_status: 'partial',
            remaining_count: 1,
            assigned_count: 2,
            produced_count: 1,
            start_released: true,
            bootstrap_job_id: 'j_boot',
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
    expect(wrapper.text()).toContain('res_status=partial')
    expect(wrapper.text()).toContain('remaining=1')
    expect(wrapper.text()).toContain('assigned=2')
    expect(wrapper.text()).toContain('produced=1')
    expect(wrapper.text()).toContain('start_released=true')
    expect(wrapper.text()).toContain('bootstrap=j_boot')
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

  it('renders session runtime fault context inside replay diagnostics', async () => {
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
        session_context: {
          runtime_fault_summary: {
            degraded: true,
            source: 'dashboard_publish',
            stage: 'task_messages',
            error: "RuntimeError('publish-boom')",
            count: 3,
            first_at: 10,
            updated_at: 12,
            breakdown: [
              { source: 'dashboard_publish', stage: 'task_messages', count: 3 },
              { source: 'dashboard_runtime_facts', stage: '', count: 1 },
            ],
          },
        },
      },
      raw_entry_count: 0,
      entry_count: 3,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Session Runtime Fault')
    expect(wrapper.text()).toContain('runtime_fault=seen')
    expect(wrapper.text()).toContain('count=3')
    expect(wrapper.text()).toContain('since=00:00:10Z')
    expect(wrapper.text()).toContain('at=00:00:12Z')
    expect(wrapper.text()).toContain('source=dashboard_publish')
    expect(wrapper.text()).toContain('stage=task_messages')
    expect(wrapper.text()).toContain("error=RuntimeError('publish-boom')")
    expect(wrapper.text()).toContain('fault=dashboard_publish/task_messages×3')
    expect(wrapper.text()).toContain('fault=dashboard_runtime_facts×1')
  })

  it('renders session world health context inside replay diagnostics', async () => {
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
        session_context: {
          world_health: {
            stale_seen: true,
            ended_stale: true,
            stale_refreshes: 2,
            max_consecutive_failures: 4,
            failure_threshold: 3,
            slow_events: 1,
            max_total_ms: 154.2,
            last_failure_layer: 'actors',
            last_error: 'actors:COMMAND_EXECUTION_ERROR',
            last_error_detail: 'Attempted to get trait from destroyed object',
          },
        },
      },
      raw_entry_count: 0,
      entry_count: 3,
      raw_entries_included: false,
      raw_entries_truncated: false,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Session Health')
    expect(wrapper.text()).toContain('Session 世界同步异常')
    expect(wrapper.text()).toContain('sync_fail=max 4/3')
    expect(wrapper.text()).toContain('stale_refresh=2')
    expect(wrapper.text()).toContain('slow=1')
    expect(wrapper.text()).toContain('max_refresh=154.2ms')
    expect(wrapper.text()).toContain('layer=actors')
    expect(wrapper.text()).toContain('last=actors:COMMAND_EXECUTION_ERROR')
    expect(wrapper.text()).toContain('detail=Attempted to get trait from destroyed object')
  })

  it('renders live unit pipeline focus detail inside the live runtime block', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      runtime_state: {
        capability_status: {
          task_id: 't_cap',
          task_label: '001',
          phase: 'dispatch',
        },
        active_tasks: {
          t_cap: { label: '001' },
        },
        active_jobs: {},
        unfulfilled_requests: [{ request_id: 'req_1' }],
        unit_reservations: [{ reservation_id: 'res_1' }],
      },
      unit_pipeline_preview: '能力在途：重坦 × 2',
      unit_pipeline_focus: {
        detail: '缺少前置建筑',
        task_id: 't_block',
        task_label: '004',
        request_count: 2,
        reservation_count: 1,
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Live Runtime')
    expect(wrapper.text()).toContain('能力在途：重坦 × 2')
    expect(wrapper.text()).toContain('focus_req=2')
    expect(wrapper.text()).toContain('focus_res=1')
    expect(wrapper.text()).toContain('focus=#004 · 缺少前置建筑')
    expect(wrapper.find('button.session-highlight-btn').text()).toContain('定位到阻塞任务')
  })

  it('dispatches diagnostics focus event from live unit pipeline focus action', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })
    const handler = vi.fn()
    window.addEventListener('theseed:focus-diagnostics-task', handler)

    try {
      bus.emit('world_snapshot', {
        runtime_state: {
          capability_status: {
            task_id: 't_cap',
            task_label: '001',
            phase: 'dispatch',
          },
          active_tasks: {
            t_cap: { label: '001' },
          },
          active_jobs: {},
        },
        unit_pipeline_preview: '能力在途：重坦 × 2',
        unit_pipeline_focus: {
          detail: '缺少前置建筑',
          task_id: 't_block',
          task_label: '004',
          request_count: 2,
          reservation_count: 1,
        },
      })
      await wrapper.vm.$nextTick()

      const button = wrapper.find('button.session-highlight-btn')
      await button.trigger('click')

      expect(handler).toHaveBeenCalledTimes(1)
      expect(handler.mock.calls[0][0].detail).toEqual({ taskId: 't_block' })
    } finally {
      window.removeEventListener('theseed:focus-diagnostics-task', handler)
    }
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
          runtime_fault_summary: {
            degraded: true,
            source: 'dashboard_publish',
            stage: 'task_messages',
            error: "RuntimeError('publish-boom')",
            count: 2,
            first_at: 10,
            updated_at: 12,
            breakdown: [
              { source: 'dashboard_publish', stage: 'task_messages', count: 2 },
              { source: 'world_sync_probe', stage: '', count: 1 },
            ],
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
    expect(wrapper.text()).toContain('runtime_fault=seen')
    expect(wrapper.text()).toContain('count=2')
    expect(wrapper.text()).toContain('since=00:00:10Z')
    expect(wrapper.text()).toContain('at=00:00:12Z')
    expect(wrapper.text()).toContain('source=dashboard_publish')
    expect(wrapper.text()).toContain('stage=task_messages')
    expect(wrapper.text()).toContain("error=RuntimeError('publish-boom')")
    expect(wrapper.text()).toContain('fault=dashboard_publish/task_messages×2')
    expect(wrapper.text()).toContain('fault=world_sync_probe×1')
  })

  it('replaces pane history from session_history and ignores live log append while browsing a historical session', async () => {
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
          is_latest: true,
          task_count: 1,
          record_count: 10,
        },
        {
          session_dir: '/tmp/old-session',
          session_name: 'old-session',
          is_current: false,
          is_latest: false,
          task_count: 1,
          record_count: 8,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#session-select').setValue('/tmp/old-session')
    await wrapper.vm.$nextTick()

    expect(send).toHaveBeenCalledWith('session_select', { session_dir: '/tmp/old-session' })

    bus.emit('session_history', {
      session_dir: '/tmp/old-session',
      is_live: false,
      log_entries: [
        {
          timestamp: 100,
          component: 'kernel',
          level: 'INFO',
          message: '历史日志',
          event: 'task_info',
          data: { task_id: 't_hist' },
        },
      ],
      benchmark_records: [
        {
          tag: 'tool_exec',
          name: 'history_bench',
          started_at: '2026-04-12T00:00:00+00:00',
          ended_at: '2026-04-12T00:00:01+00:00',
          duration_ms: 1,
          metadata: {},
        },
      ],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('历史日志')
    expect(wrapper.text()).toContain('tool_exec')

    bus.emit('log_entry', {
      timestamp: 200,
      component: 'kernel',
      level: 'INFO',
      message: '实时日志',
      event: 'task_info',
      data: { task_id: 't_live' },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).not.toContain('实时日志')
  })

  it('renders historical operator messages from session_history and ignores live operator append while browsing a historical session', async () => {
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
          is_latest: true,
          task_count: 1,
          record_count: 10,
        },
        {
          session_dir: '/tmp/old-session',
          session_name: 'old-session',
          is_current: false,
          is_latest: false,
          task_count: 1,
          record_count: 8,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#session-select').setValue('/tmp/old-session')
    await wrapper.vm.$nextTick()

    bus.emit('session_history', {
      session_dir: '/tmp/old-session',
      is_live: false,
      log_entries: [
        {
          timestamp: 100,
          component: 'dashboard_publish',
          level: 'INFO',
          message: '副官收到指令',
          event: 'adjutant_response_sent',
          data: { task_id: 't_hist', content: '副官收到指令' },
        },
        {
          timestamp: 101,
          component: 'dashboard_publish',
          level: 'INFO',
          message: '任务已取消',
          event: 'player_notification_sent',
          data: { content: '任务已取消', data: { task_id: 't_hist' } },
        },
        {
          timestamp: 102,
          component: 'dashboard_publish',
          level: 'INFO',
          message: '其他任务通知',
          event: 'player_notification_sent',
          data: { content: '其他任务通知', data: { task_id: 't_other' } },
        },
      ],
      player_visible_entries: [
        {
          kind: 'adjutant',
          timestamp: 100,
          task_id: 't_hist',
          content: '副官收到指令',
        },
        {
          kind: 'notification',
          timestamp: 101,
          task_id: 't_hist',
          content: '任务已取消',
        },
        {
          kind: 'notification',
          timestamp: 102,
          task_id: 't_other',
          content: '其他任务通知',
        },
        {
          kind: 'task_message',
          timestamp: 103,
          task_id: 't_hist',
          content: '侦察等待增援',
          message_type: 'task_warning',
        },
      ],
      error_entries: [
        {
          timestamp: 104,
          task_id: 't_hist',
          content: "历史错误 | RuntimeError('boom')",
          component: 'dashboard_publish',
          event: 'dashboard_publish_stage_failed',
          level: 'ERROR',
        },
      ],
      benchmark_records: [],
    })
    bus.emit('session_task_catalog', {
      tasks: [
        { task_id: 't_hist', raw_text: '历史任务', status: 'running' },
        { task_id: 't_other', raw_text: '其他任务', status: 'running' },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_hist')
    await wrapper.vm.$nextTick()

    const operatorSurface = wrapper.find('.session-operator-strip').text()
    expect(operatorSurface).toContain('Operator Surface')
    expect(operatorSurface).toContain('adjutant@00:01:40Z')
    expect(operatorSurface).toContain('notify@00:01:41Z')
    expect(operatorSurface).toContain('task_warning@00:01:43Z')
    expect(operatorSurface).toContain('error@00:01:44Z')
    expect(operatorSurface).toContain('副官收到指令')
    expect(operatorSurface).toContain('任务已取消')
    expect(operatorSurface).toContain('侦察等待增援')
    expect(operatorSurface).toContain("历史错误 | RuntimeError('boom')")
    expect(operatorSurface).not.toContain('其他任务通知')

    bus.emit('query_response', {
      task_id: 't_live',
      answer: '实时副官回复',
    }, 200)
    bus.emit('player_notification', {
      content: '实时通知',
      data: { task_id: 't_live' },
    }, 201)
    bus.emit('task_message', {
      task_id: 't_live',
      type: 'task_warning',
      content: '实时任务警告',
    }, 202)
    bus.emit('error', {
      task_id: 't_live',
      error: '实时错误',
    }, 203)
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).not.toContain('实时副官回复')
    expect(wrapper.text()).not.toContain('实时通知')
    expect(wrapper.text()).not.toContain('实时任务警告')
    expect(wrapper.text()).not.toContain('实时错误')
  })

  it('prefers structured historical operator entries over player_visible fallback', async () => {
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
          is_latest: true,
          task_count: 1,
          record_count: 10,
        },
        {
          session_dir: '/tmp/old-session',
          session_name: 'old-session',
          is_current: false,
          is_latest: false,
          task_count: 1,
          record_count: 8,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#session-select').setValue('/tmp/old-session')
    await wrapper.vm.$nextTick()

    bus.emit('session_history', {
      session_dir: '/tmp/old-session',
      is_live: false,
      log_entries: [
        {
          timestamp: 100,
          component: 'dashboard_publish',
          level: 'INFO',
          message: 'fallback-raw-operator',
          event: 'player_notification_sent',
          data: { content: 'fallback-raw-operator', data: { task_id: 't_hist' } },
        },
      ],
      player_visible_entries: [],
      query_response_entries: [
        {
          timestamp: 101,
          task_id: 't_hist',
          answer: '结构化副官回复',
          response_type: 'command',
          ok: true,
        },
      ],
      notification_entries: [
        {
          timestamp: 102,
          task_id: 't_hist',
          content: '结构化通知',
        },
      ],
      error_entries: [
        {
          timestamp: 102.5,
          task_id: 't_hist',
          content: '结构化错误',
          component: 'runtime',
          event: 'runtime_error',
          level: 'ERROR',
        },
      ],
      task_message_entries: [
        {
          timestamp: 103,
          task_id: 't_hist',
          message_id: 'msg_warn',
          message_type: 'task_warning',
          content: '结构化任务警告',
          priority: 60,
        },
      ],
      benchmark_records: [],
    })
    bus.emit('session_task_catalog', {
      tasks: [
        { task_id: 't_hist', raw_text: '历史任务', status: 'running' },
      ],
    })
    await wrapper.vm.$nextTick()

    const operatorSurface = wrapper.find('.session-operator-strip').text()
    expect(operatorSurface).toContain('结构化副官回复')
    expect(operatorSurface).toContain('结构化通知')
    expect(operatorSurface).toContain('结构化错误')
    expect(operatorSurface).toContain('结构化任务警告')
    expect(operatorSurface).not.toContain('fallback-raw-operator')
  })

  it('surfaces live websocket error frames in the operator strip for the current session', async () => {
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
          is_latest: true,
          task_count: 1,
          record_count: 10,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    bus.emit('error', {
      task_id: 't_live',
      error: '未知消息类型',
    }, 200)
    await wrapper.vm.$nextTick()

    const operatorSurface = wrapper.find('.session-operator-strip').text()
    expect(operatorSurface).toContain('error@00:03:20Z')
    expect(operatorSurface).toContain('未知消息类型')
  })

  it('falls back to raw session log operator messages and preserves task-focus filtering semantics', async () => {
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
          is_latest: true,
          task_count: 1,
          record_count: 10,
        },
        {
          session_dir: '/tmp/old-session',
          session_name: 'old-session',
          is_current: false,
          is_latest: false,
          task_count: 2,
          record_count: 12,
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#session-select').setValue('/tmp/old-session')
    await wrapper.vm.$nextTick()

    bus.emit('session_history', {
      session_dir: '/tmp/old-session',
      is_live: false,
      log_entries: [
        {
          timestamp: 100,
          component: 'dashboard_publish',
          level: 'INFO',
          message: '副官收到指令',
          event: 'adjutant_response_sent',
          data: { task_id: 't_hist', content: '副官收到指令' },
        },
        {
          timestamp: 101,
          component: 'dashboard_publish',
          level: 'INFO',
          message: '全局提醒',
          event: 'player_notification_sent',
          data: { content: '全局提醒', data: {} },
        },
        {
          timestamp: 102,
          component: 'kernel',
          level: 'INFO',
          message: '历史警告',
          event: 'task_message_registered',
          data: {
            task_id: 't_hist',
            message_type: 'task_warning',
            content: '历史警告',
          },
        },
        {
          timestamp: 103,
          component: 'kernel',
          level: 'INFO',
          message: '历史提示',
          event: 'task_info',
          data: {
            task_id: 't_hist',
            content: '历史提示',
          },
        },
        {
          timestamp: 104,
          component: 'kernel',
          level: 'WARNING',
          message: '其他任务警告',
          event: 'task_warning',
          data: {
            task_id: 't_other',
            content: '其他任务警告',
          },
        },
      ],
      benchmark_records: [],
    })
    bus.emit('session_task_catalog', {
      tasks: [
        { task_id: 't_hist', raw_text: '历史任务', status: 'running' },
        { task_id: 't_other', raw_text: '其他任务', status: 'running' },
      ],
    })
    await wrapper.vm.$nextTick()

    const allOperatorSurface = wrapper.find('.session-operator-strip').text()
    expect(allOperatorSurface).toContain('Operator Surface')
    expect(allOperatorSurface).toContain('adjutant@00:01:40Z')
    expect(allOperatorSurface).toContain('notify@00:01:41Z')
    expect(allOperatorSurface).toContain('task_warning@00:01:42Z')
    expect(allOperatorSurface).toContain('task_info@00:01:43Z')
    expect(allOperatorSurface).toContain('副官收到指令')
    expect(allOperatorSurface).toContain('全局提醒')
    expect(allOperatorSurface).toContain('历史警告')
    expect(allOperatorSurface).toContain('历史提示')
    expect(allOperatorSurface).toContain('其他任务警告')

    await wrapper.find('#task-trace-select').setValue('t_hist')
    await wrapper.vm.$nextTick()

    const focusedOperatorSurface = wrapper.find('.session-operator-strip').text()
    expect(focusedOperatorSurface).toContain('副官收到指令')
    expect(focusedOperatorSurface).toContain('全局提醒')
    expect(focusedOperatorSurface).toContain('历史警告')
    expect(focusedOperatorSurface).toContain('历史提示')
    expect(focusedOperatorSurface).not.toContain('其他任务警告')
  })

  it('renders compact task rollup hints in session selector options', async () => {
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
          task_rollup: {
            total: 3,
            non_terminal: 2,
            terminal: 1,
            by_status: {
              running: 2,
              partial: 1,
            },
          },
        },
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
          is_latest: true,
          task_rollup: {
            total: 5,
            non_terminal: 0,
            terminal: 5,
            by_status: {
              succeeded: 3,
              failed: 1,
              aborted: 1,
            },
          },
        },
      ],
      selected_session_dir: '/tmp/live-session',
    })
    await wrapper.vm.$nextTick()

    const options = wrapper.findAll('#session-select option').map((item) => item.text())
    expect(options).toContain('live-session · live/nt=2/partial=1')
    expect(options).toContain('history-session · latest/failed=1/aborted=1')
  })

  it('renders stale and runtime-fault scan hints directly in session selector options', async () => {
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
          session_dir: '/tmp/degraded-session',
          session_name: 'degraded-session',
          is_current: true,
          world_health: {
            stale_seen: true,
            ended_stale: true,
            max_consecutive_failures: 4,
            failure_threshold: 3,
          },
          runtime_fault_summary: {
            degraded: true,
            source: 'dashboard_publish',
            stage: 'task_messages',
            error: "RuntimeError('publish-boom')",
            count: 2,
            first_at: 10,
            updated_at: 12,
          },
        },
        {
          session_dir: '/tmp/healthy-session',
          session_name: 'healthy-session',
          is_latest: true,
          world_health: {},
          runtime_fault_summary: {},
        },
      ],
      selected_session_dir: '/tmp/degraded-session',
    })
    await wrapper.vm.$nextTick()

    const options = wrapper.findAll('#session-select option').map((item) => item.text())
    expect(options).toContain('degraded-session · live/faultx2@00:00:12Z/stale/sync=4/3')
    expect(options).toContain('healthy-session · latest')
    expect(options).not.toContain('healthy-session · latest/fault')
    expect(options).not.toContain('healthy-session · latest/stale')
  })

  it('renders disconnect scan hints directly in session selector options', async () => {
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
          session_dir: '/tmp/disconnected-session',
          session_name: 'disconnected-session',
          is_current: true,
          world_health: {
            stale_seen: true,
            ended_stale: true,
            disconnect_seen: true,
            ended_disconnected: true,
            max_consecutive_failures: 4,
            failure_threshold: 3,
          },
        },
      ],
      selected_session_dir: '/tmp/disconnected-session',
    })
    await wrapper.vm.$nextTick()

    const options = wrapper.findAll('#session-select option').map((item) => item.text())
    expect(options).toContain('disconnected-session · live/disconnect/sync=4/3')
    expect(wrapper.text()).toContain('Session 游戏连接断开')
  })

  it('renders selected-session highlights and focuses the chosen task', async () => {
    const bus = createBus()
    const send = vi.fn(() => true)
    const wrapper = mount(DiagPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('session_catalog', {
      sessions: [
        {
          session_dir: '/tmp/history-session',
          session_name: 'history-session',
        },
      ],
      selected_session_dir: '/tmp/history-session',
    })
    bus.emit('session_task_catalog', {
      session_dir: '/tmp/history-session',
      tasks: [
        {
          task_id: 't_fail',
          label: '003',
          raw_text: '建造雷达',
          status: 'failed',
          timestamp: 300,
          summary: '缺少前置建筑',
        },
        {
          task_id: 't_partial',
          label: '004',
          raw_text: '推进前线',
          status: 'partial',
          timestamp: 200,
          summary: '部队不足，部分完成',
        },
        {
          task_id: 't_wait',
          label: '005',
          raw_text: '侦察地图',
          status: 'running',
          timestamp: 100,
          triage: {
            status_line: '等待能力模块补足步兵',
            waiting_reason: 'unit_request',
          },
        },
        {
          task_id: 't_ok',
          label: '006',
          raw_text: '发展经济',
          status: 'succeeded',
          timestamp: 50,
          summary: '矿场已建成',
        },
      ],
    })
    await wrapper.vm.$nextTick()

    const highlightButtons = wrapper.findAll('.session-highlight-btn')
    expect(highlightButtons).toHaveLength(3)
    expect(wrapper.text()).toContain('003 · 缺少前置建筑')
    expect(wrapper.text()).toContain('004 · 部队不足，部分完成')
    expect(wrapper.text()).toContain('005 · 等待能力模块补足步兵')
    expect(wrapper.text()).not.toContain('006 · 矿场已建成')

    await highlightButtons[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('#task-trace-select').element.value).toBe('t_fail')
    expect(send.mock.calls.some(([type, payload]) => (
      type === 'task_replay_request'
      && payload.task_id === 't_fail'
      && payload.session_dir === '/tmp/history-session'
    ))).toBe(true)
  })

  it('compacts consecutive duplicate trace entries into repeat counts', async () => {
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
          task_id: 't_wait',
          raw_text: '侦察地图',
          status: 'running',
          timestamp: 100,
          created_at: 90,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('#task-trace-select').setValue('t_wait')
    await wrapper.vm.$nextTick()

    const repeatedPayload = {
      task_id: 't_wait',
      content: '等待能力模块补兵',
    }
    bus.emit('task_message', repeatedPayload, 100)
    bus.emit('task_message', repeatedPayload, 101)
    bus.emit('task_message', repeatedPayload, 102)
    bus.emit('task_message', {
      task_id: 't_wait',
      content: '收到新的侦察结果',
    }, 103)
    await wrapper.vm.$nextTick()

    const traceEntries = wrapper.findAll('.trace-entry')
    expect(traceEntries).toHaveLength(2)
    expect(traceEntries[0].text()).toContain('等待能力模块补兵')
    expect(traceEntries[0].text()).toContain('×3')
    expect(traceEntries[1].text()).toContain('收到新的侦察结果')
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

  it('renders disconnect details distinctly from generic world-sync stale state', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('world_snapshot', {
      stale: true,
      disconnected: true,
      consecutive_refresh_failures: 5,
      failure_threshold: 3,
      last_refresh_error: 'actors:CONNECTION_ERROR: connection refused',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('游戏连接断开')
    expect(wrapper.text()).toContain('world=disconnect')
    expect(wrapper.text()).toContain('failures=5/3')
    expect(wrapper.text()).toContain('error=actors:CONNECTION_ERROR: connection refused')
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

  it('renders runtime fault detail from world_snapshot', async () => {
    const bus = createBus()
    const wrapper = mount(DiagPanel, {
      props: {
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
        count: 4,
        first_at: 10,
        updated_at: 12,
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('运行时降级')
    expect(wrapper.text()).toContain('count=4')
    expect(wrapper.text()).toContain('since=00:00:10Z')
    expect(wrapper.text()).toContain('at=00:00:12Z')
    expect(wrapper.text()).toContain('source=dashboard_publish')
    expect(wrapper.text()).toContain('stage=task_messages')
    expect(wrapper.text()).toContain("error=RuntimeError('publish-boom')")
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

  it('refreshes selected live replay when runtime fault truth changes', async () => {
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
        runtime_fault_state: {
          degraded: true,
          source: 'dashboard_publish',
          stage: 'task_messages',
          error: "RuntimeError('publish-boom')",
        },
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

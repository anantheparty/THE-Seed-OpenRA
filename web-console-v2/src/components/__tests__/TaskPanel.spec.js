import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaskPanel from '../TaskPanel.vue'

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

describe('TaskPanel', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('keeps capability tasks first and collapses terminal tasks by default', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_done',
          raw_text: '完成的普通任务',
          status: 'succeeded',
          timestamp: 100,
          priority: 10,
          jobs: [{ job_id: 'j_done', expert_type: 'ReconExpert', status: 'succeeded', summary: 'done' }],
          job_count: 1,
        },
        {
          task_id: 't_run',
          raw_text: '运行中的普通任务',
          status: 'running',
          timestamp: 200,
          priority: 20,
          jobs: [{ job_id: 'j_run', expert_type: 'MovementExpert', status: 'running', summary: 'moving' }],
          job_count: 1,
        },
        {
          task_id: 't_cap',
          raw_text: '常驻能力任务',
          status: 'running',
          timestamp: 50,
          priority: 100,
          is_capability: true,
          jobs: [{ job_id: 'j_cap', expert_type: 'EconomyExpert', status: 'running', summary: 'economy' }],
          job_count: 1,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    const cards = wrapper.findAll('.task-card')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('常驻能力任务')
    expect(cards[1].text()).toContain('运行中的普通任务')
    expect(cards[2].text()).toContain('完成的普通任务')

    expect(cards[0].find('.task-details').exists()).toBe(true)
    expect(cards[1].find('.task-details').exists()).toBe(true)
    expect(cards[2].find('.task-details').exists()).toBe(false)
    expect(cards[2].find('.task-collapsed-hint').exists()).toBe(false)
    expect(cards[2].find('.expand-btn').text()).toBe('▸')
  })

  it('merges task_update into the latest task view', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_1',
          raw_text: '建造电厂',
          status: 'pending',
          timestamp: 100,
          priority: 10,
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [],
    })
    bus.emit('task_update', {
      task_id: 't_1',
      status: 'running',
      triage: { status_line: '正在等待前置完成' },
      jobs: [{ job_id: 'j_1', expert_type: 'EconomyExpert', status: 'running', summary: 'queueing' }],
      job_count: 1,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('正在等待前置完成')
    expect(wrapper.find('.status-badge').text()).toBe('running')
    expect(wrapper.find('.job-expert').text()).toBe('EconomyExpert')
  })

  it('renders structured triage metadata chips when present', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_2',
          raw_text: '发展经济',
          status: 'running',
          timestamp: 100,
          priority: 20,
          triage: {
            status_line: '等待能力模块交付单位：重坦 × 2',
            waiting_reason: 'unit_reservation',
            blocking_reason: 'missing_prerequisite',
            reservation_ids: ['res_1'],
            reservation_preview: '重坦 × 2 · 缺少前置',
            reservation_status: 'pending',
            remaining_count: 2,
            assigned_count: 1,
            produced_count: 1,
            start_released: true,
            bootstrap_job_id: 'j_boot',
            world_stale: true,
            world_sync_failures: 3,
            world_sync_failure_threshold: 2,
            world_sync_error: 'actors:COMMAND_EXECUTION_ERROR',
          },
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('waiting=unit_reservation')
    expect(wrapper.text()).toContain('blocker=missing_prerequisite')
    expect(wrapper.text()).toContain('reservations=1')
    expect(wrapper.text()).toContain('reservation=重坦 × 2 · 缺少前置')
    expect(wrapper.text()).toContain('res_status=pending')
    expect(wrapper.text()).toContain('remaining=2')
    expect(wrapper.text()).toContain('assigned=1')
    expect(wrapper.text()).toContain('produced=1')
    expect(wrapper.text()).toContain('start_released=yes')
    expect(wrapper.text()).toContain('bootstrap=j_boot')
    expect(wrapper.text()).toContain('world=stale')
    expect(wrapper.text()).toContain('sync_fail=3/2')
    expect(wrapper.text()).toContain('sync=actors:COMMAND_EXECUTION_ERROR')
  })

  it('offers command_cancel on pending questions', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(TaskPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_question',
          raw_text: 'V',
          status: 'running',
          timestamp: 100,
          priority: 20,
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [
        {
          message_id: 'msg_question',
          task_id: 't_question',
          question: '任务"V"已启动，但目标不明确。请指定您希望执行的具体行动：',
          options: ['攻击', '侦察'],
          default_option: '攻击',
          timeout_s: 60,
        },
      ],
    })
    await wrapper.vm.$nextTick()

    const buttons = wrapper.findAll('.question-card .option-btn')
    expect(buttons.map((item) => item.text())).toEqual(['攻击', '侦察', '取消任务'])

    await buttons[2].trigger('click')

    expect(send).toHaveBeenCalledWith('command_cancel', { task_id: 't_question' })
    const updatedButtons = wrapper.findAll('.question-card .option-btn')
    expect(updatedButtons[0].attributes('disabled')).toBeDefined()
    expect(updatedButtons[1].attributes('disabled')).toBeDefined()
    expect(updatedButtons[2].attributes('disabled')).toBeDefined()
  })

  it('renders ownership chips when a task already controls units', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_owned',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 100,
          priority: 20,
          triage: {
            status_line: '执行中 | group=2',
            has_active_group: true,
            active_group_size: 2,
          },
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('执行中 | group=2')
    expect(wrapper.text()).toContain('owned=2')
  })

  it('updates task age labels reactively over time', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-13T00:00:00Z'))
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    try {
      bus.emit('task_list', {
        tasks: [
          {
            task_id: 't_age',
            raw_text: '观察时间标签',
            status: 'running',
            timestamp: Math.floor(Date.now() / 1000) - 10,
            priority: 5,
            jobs: [],
            job_count: 0,
          },
        ],
        pending_questions: [],
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.text()).toContain('10s ago')

      await vi.advanceTimersByTimeAsync(2000)
      await wrapper.vm.$nextTick()

      expect(wrapper.text()).toContain('12s ago')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps completed experts collapsed by default until expanded', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_mix',
          raw_text: '推进基地建设',
          status: 'running',
          timestamp: 100,
          priority: 20,
          jobs: [
            { job_id: 'j_run', expert_type: 'EconomyExpert', status: 'running', summary: 'queueing power' },
            { job_id: 'j_done', expert_type: 'ReconExpert', status: 'succeeded', summary: 'scout done' },
          ],
          job_count: 2,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('EconomyExpert')
    expect(wrapper.text()).not.toContain('ReconExpert')
    expect(wrapper.text()).toContain('已完成 Experts · 1')

    await wrapper.find('.completed-jobs-toggle').trigger('click')

    expect(wrapper.text()).toContain('ReconExpert')
    expect(wrapper.text()).toContain('scout done')
  })

  it('preserves completed-expert expansion across task updates', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_mix',
          raw_text: '发展科技',
          status: 'running',
          timestamp: 100,
          priority: 20,
          jobs: [
            { job_id: 'j_done', expert_type: 'ReconExpert', status: 'succeeded', summary: 'first summary' },
          ],
          job_count: 1,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('.completed-jobs-toggle').trigger('click')
    expect(wrapper.text()).toContain('ReconExpert')
    expect(wrapper.text()).toContain('first summary')

    bus.emit('task_update', {
      task_id: 't_mix',
      jobs: [
        { job_id: 'j_done', expert_type: 'ReconExpert', status: 'succeeded', summary: 'updated summary' },
      ],
      job_count: 1,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('ReconExpert')
    expect(wrapper.text()).toContain('updated summary')
  })

  it('dispatches diagnostics focus events for a task', async () => {
    const bus = createBus()
    const wrapper = mount(TaskPanel, {
      props: {
        send: () => {},
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_focus',
          raw_text: '推进前线',
          status: 'running',
          timestamp: 100,
          priority: 20,
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    const events = []
    const handler = (event) => events.push(event.detail)
    window.addEventListener('theseed:focus-diagnostics-task', handler)
    try {
      await wrapper.find('.diag-focus-btn').trigger('click')
    } finally {
      window.removeEventListener('theseed:focus-diagnostics-task', handler)
    }

    expect(events).toEqual([{ taskId: 't_focus' }])
  })

  it('refreshes task age labels over time without requiring task updates', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-12T00:00:00Z'))
    try {
      const bus = createBus()
      const wrapper = mount(TaskPanel, {
        props: {
          send: () => {},
          on: bus.on,
        },
      })

      bus.emit('task_list', {
        tasks: [
          {
            task_id: 't_age',
            raw_text: '持续监控前线',
            status: 'running',
            timestamp: Date.now() / 1000 - 61,
            priority: 30,
            jobs: [],
            job_count: 0,
          },
        ],
        pending_questions: [],
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.task-meta').text()).toContain('1m ago')

      await vi.advanceTimersByTimeAsync(60_000)
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.task-meta').text()).toContain('2m ago')
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('sends command_cancel for a running non-capability task', async () => {
    const bus = createBus()
    const send = vi.fn()
    const wrapper = mount(TaskPanel, {
      props: {
        send,
        on: bus.on,
      },
    })

    bus.emit('task_list', {
      tasks: [
        {
          task_id: 't_cancel',
          raw_text: '取消中的任务',
          status: 'running',
          timestamp: 100,
          priority: 20,
          jobs: [],
          job_count: 0,
        },
      ],
      pending_questions: [],
    })
    await wrapper.vm.$nextTick()

    await wrapper.find('.cancel-btn').trigger('click')

    expect(send).toHaveBeenCalledWith('command_cancel', { task_id: 't_cancel' })
  })
})

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

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
    expect(wrapper.text()).toContain('world=stale')
    expect(wrapper.text()).toContain('sync_fail=3/2')
    expect(wrapper.text()).toContain('sync=actors:COMMAND_EXECUTION_ERROR')
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
})

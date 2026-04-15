import { afterEach, describe, expect, it, vi } from 'vitest'

import { prepareAsrUpload } from '../asrUpload.js'

describe('prepareAsrUpload', () => {
  const originalAudioContext = window.AudioContext
  const originalOfflineAudioContext = window.OfflineAudioContext

  afterEach(() => {
    window.AudioContext = originalAudioContext
    window.OfflineAudioContext = originalOfflineAudioContext
  })

  it('converts browser-recorded audio into wav uploads', async () => {
    const close = vi.fn().mockResolvedValue(undefined)
    const decodeAudioData = vi.fn().mockResolvedValue({ duration: 0.001 })
    const startRendering = vi.fn().mockResolvedValue({
      sampleRate: 16000,
      getChannelData: vi.fn(() => new Float32Array([0, 0.25, -0.25])),
    })

    class FakeAudioContext {
      decodeAudioData = decodeAudioData
      close = close
    }

    class FakeOfflineAudioContext {
      constructor() {
        this.destination = {}
      }

      createBufferSource() {
        return {
          connect: vi.fn(),
          start: vi.fn(),
          set buffer(value) {
            this._buffer = value
          },
        }
      }

      startRendering() {
        return startRendering()
      }
    }

    window.AudioContext = FakeAudioContext
    window.OfflineAudioContext = FakeOfflineAudioContext

    const result = await prepareAsrUpload(
      new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/webm;codecs=opus' }),
      16000,
    )

    expect(result.filename).toBe('recording.wav')
    expect(result.query).toBe('&format=wav')
    expect(result.blob.type).toBe('audio/wav')
    expect(close).toHaveBeenCalledTimes(1)
    expect(startRendering).toHaveBeenCalledTimes(1)
  })

  it('falls back to the original blob when browser audio contexts are unavailable', async () => {
    window.AudioContext = undefined
    window.OfflineAudioContext = undefined

    const blob = new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/webm;codecs=opus' })
    const result = await prepareAsrUpload(blob, 16000)

    expect(result.blob).toBe(blob)
    expect(result.filename).toBe('recording.webm')
    expect(result.query).toBe('')
  })
})

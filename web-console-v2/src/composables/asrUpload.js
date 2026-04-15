function writeAscii(view, offset, value) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i))
  }
}

function fallbackUpload(blob) {
  const mime = String(blob?.type || '').toLowerCase()
  const extension = mime.includes('ogg') ? 'ogg' : mime.includes('wav') ? 'wav' : 'webm'
  const query = extension === 'wav' ? '&format=wav' : ''
  return {
    blob,
    filename: `recording.${extension}`,
    query,
  }
}

export function encodeWav(samples, sampleRate) {
  const frameCount = samples.length
  const buffer = new ArrayBuffer(44 + frameCount * 2)
  const view = new DataView(buffer)

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + frameCount * 2, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, frameCount * 2, true)

  let offset = 44
  for (let i = 0; i < frameCount; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i] || 0))
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
    offset += 2
  }
  return buffer
}

export async function prepareAsrUpload(blob, sampleRate = 16000) {
  if (blob.type === 'audio/wav') {
    return {
      blob,
      filename: 'recording.wav',
      query: '&format=wav',
    }
  }

  const AudioContextCtor = window.AudioContext || window.webkitAudioContext
  const OfflineAudioContextCtor = window.OfflineAudioContext || window.webkitOfflineAudioContext
  if (!AudioContextCtor || !OfflineAudioContextCtor) {
    return fallbackUpload(blob)
  }

  const audioContext = new AudioContextCtor()
  try {
    try {
      const encoded = await blob.arrayBuffer()
      const decoded = await audioContext.decodeAudioData(encoded.slice(0))
      const frameCount = Math.max(1, Math.ceil(decoded.duration * sampleRate))
      const offline = new OfflineAudioContextCtor(1, frameCount, sampleRate)
      const source = offline.createBufferSource()
      source.buffer = decoded
      source.connect(offline.destination)
      source.start(0)
      const rendered = await offline.startRendering()
      const wavBuffer = encodeWav(rendered.getChannelData(0), rendered.sampleRate)
      return {
        blob: new Blob([wavBuffer], { type: 'audio/wav' }),
        filename: 'recording.wav',
        query: '&format=wav',
      }
    } catch (_) {
      return fallbackUpload(blob)
    }
  } finally {
    if (typeof audioContext.close === 'function') {
      try {
        await audioContext.close()
      } catch (_) {
        // ignore close failures
      }
    }
  }
}

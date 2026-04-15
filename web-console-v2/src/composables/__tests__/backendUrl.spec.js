import { describe, expect, it } from 'vitest'

import { resolveBackendHttpBaseUrl } from '../backendUrl.js'

describe('resolveBackendHttpBaseUrl', () => {
  it('routes dev-server pages back to backend port 8765 by default', () => {
    expect(
      resolveBackendHttpBaseUrl(
        { protocol: 'http:', hostname: 'localhost', port: '5173' },
        { DEV: true },
      ),
    ).toBe('http://localhost:8765')
  })

  it('keeps same-origin routing outside dev mode', () => {
    expect(
      resolveBackendHttpBaseUrl(
        { protocol: 'https:', hostname: 'demo.example.com', port: '' },
        { DEV: false },
      ),
    ).toBe('https://demo.example.com')
  })

  it('prefers explicit backend override when configured', () => {
    expect(
      resolveBackendHttpBaseUrl(
        { protocol: 'http:', hostname: 'localhost', port: '5173' },
        { DEV: true, VITE_BACKEND_HTTP_URL: 'https://voice.example.com/' },
      ),
    ).toBe('https://voice.example.com')
  })
})

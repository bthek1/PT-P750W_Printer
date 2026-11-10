import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StatusResponse } from './api'
import { POLL_MS, usePrinterStatus } from './usePrinterStatus'

const ONLINE: StatusResponse = { connected: true, busy: false, device: '/dev/usb/lp3', error: null, status: null }

let serverUp: boolean
let fetchMock: ReturnType<typeof vi.fn>

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  serverUp = true
  fetchMock = vi.fn(() =>
    serverUp
      ? Promise.resolve(new Response(JSON.stringify(ONLINE)))
      : Promise.reject(new TypeError('Failed to fetch')),
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  setHidden(false)
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// Lets the pending fetch promise settle, then moves the clock.
async function advance(ms: number) {
  await act(() => vi.advanceTimersByTimeAsync(ms))
}

describe('usePrinterStatus', () => {
  it('starts connecting, then reports the server state', async () => {
    const { result } = renderHook(() => usePrinterStatus())
    expect(result.current[0]).toEqual({ state: 'connecting' })
    await advance(0)
    expect(result.current[0]).toEqual({ state: 'online', data: ONLINE })
  })

  it(`polls every ${POLL_MS} ms`, async () => {
    renderHook(() => usePrinterStatus())
    await advance(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await advance(POLL_MS)
    await advance(POLL_MS)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('reconnects by itself when the server comes back', async () => {
    serverUp = false
    const { result } = renderHook(() => usePrinterStatus())
    await advance(0)
    expect(result.current[0]).toMatchObject({ state: 'offline', error: expect.stringContaining('Failed to fetch') })
    serverUp = true
    await advance(POLL_MS)
    expect(result.current[0].state).toBe('online')
  })

  it('stops polling while the tab is hidden and refreshes when shown', async () => {
    renderHook(() => usePrinterStatus())
    await advance(0)
    setHidden(true)
    await advance(POLL_MS * 3)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    setHidden(false)
    await advance(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('refresh() polls immediately', async () => {
    const { result } = renderHook(() => usePrinterStatus())
    await advance(0)
    act(() => result.current[1]())
    await advance(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('stops polling after unmount', async () => {
    const { unmount } = renderHook(() => usePrinterStatus())
    await advance(0)
    unmount()
    await advance(POLL_MS * 3)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

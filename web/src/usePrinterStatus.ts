import { useCallback, useEffect, useState } from 'react'
import { fetchStatus, type StatusResponse } from './api'

export const POLL_MS = 2000

export type Connection =
  | { state: 'connecting' }
  | { state: 'offline'; error: string }
  | { state: 'online'; data: StatusResponse }

/** Polls the server for printer status and reconnects by itself after failures. */
export function usePrinterStatus(): [Connection, () => void] {
  const [connection, setConnection] = useState<Connection>({ state: 'connecting' })
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined

    const poll = async () => {
      // Each poll sends a command to the printer. Skip it while the tab is
      // hidden so a forgotten tab does not keep the device busy.
      if (!document.hidden) {
        let next: Connection
        try {
          next = { state: 'online', data: await fetchStatus(controller.signal) }
        } catch (error) {
          next = { state: 'offline', error: String(error) }
        }
        // Unmounted or restarted by refresh() while the request was in flight.
        if (controller.signal.aborted) return
        setConnection(next)
      }
      timer = window.setTimeout(poll, POLL_MS)
    }

    poll()
    const onVisible = () => {
      if (!document.hidden) refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [tick, refresh])

  return [connection, refresh]
}

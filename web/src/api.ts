export type PrinterInfo = {
  model_code: number
  status_type: string
  errors: string[]
  tape_mm: number
  media: string
  tape_colour: string
  text_colour: string
  raw: string
}

export type StatusResponse = {
  connected: boolean
  busy: boolean
  device: string | null
  error: string | null
  status: PrinterInfo | null
  checked_at?: string
}

export type PrintResult = { ok: true; raster_lines: number; length_mm: number }

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const response = await fetch('/api/status', { signal })
  if (!response.ok) throw new Error(`status request failed: HTTP ${response.status}`)
  return response.json()
}

export async function printLabel(text: string, cut: boolean): Promise<PrintResult> {
  const response = await fetch('/api/print', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, cut }),
  })
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }))
  if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`)
  return data
}

export function previewUrl(text: string, tapeMm: number): string {
  return `/api/preview?${new URLSearchParams({ text, tape: String(tapeMm) })}`
}

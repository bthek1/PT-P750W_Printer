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

// Mirrors printing.Style on the server, which validates every field.
export type LabelStyle = {
  font: 'sans' | 'condensed' | 'serif' | 'mono'
  bold: boolean
  italic: boolean
  underline: boolean
  strike: boolean
  outline: boolean
  invert: boolean
  border: boolean
  align: 'left' | 'center' | 'right'
  case: 'none' | 'upper' | 'lower' | 'title'
  size_pt: number | null
  line_spacing: 1 | 1.5 | 2
  direction: 'horizontal' | 'vertical'
}

export const DEFAULT_STYLE: LabelStyle = {
  font: 'sans',
  bold: true,
  italic: false,
  underline: false,
  strike: false,
  outline: false,
  invert: false,
  border: false,
  align: 'center',
  case: 'none',
  size_pt: null,
  line_spacing: 1,
  direction: 'horizontal',
}

export type Flag = 'bold' | 'italic' | 'underline' | 'strike' | 'outline' | 'invert' | 'border'

export function toggleFlag(style: LabelStyle, flag: Flag): LabelStyle {
  return { ...style, [flag]: !style[flag] }
}

export type PrintResult = { ok: true; raster_lines: number; length_mm: number }

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const response = await fetch('/api/status', { signal })
  if (!response.ok) throw new Error(`status request failed: HTTP ${response.status}`)
  return response.json()
}

export async function printLabel(text: string, cut: boolean, style = DEFAULT_STYLE): Promise<PrintResult> {
  const response = await fetch('/api/print', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, cut, style }),
  })
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }))
  if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`)
  return data
}

/** The font size, in points, that the renderer picks for this label. */
export async function fetchFitSize(previewUrl: string, signal?: AbortSignal): Promise<number> {
  const response = await fetch(previewUrl.replace('/api/preview', '/api/fit'), { signal })
  if (!response.ok) throw new Error(`fit request failed: HTTP ${response.status}`)
  return (await response.json()).size_pt
}

export function previewUrl(text: string, tapeMm: number, style = DEFAULT_STYLE): string {
  return `/api/preview?${new URLSearchParams({ text, tape: String(tapeMm), style: JSON.stringify(style) })}`
}

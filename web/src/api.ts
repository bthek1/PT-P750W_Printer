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

export const MAX_COPIES = 99
/** Labels per print job (labels x copies), the limit of the printer's label counter. */
export const MAX_PAGES = 99

export type PrintResult = { ok: true; labels: number; raster_lines: number; length_mm: number }

/** fullCut: a full cut follows the label; otherwise a half cut keeps the next one on the same backing. */
export type Label = { text: string; fullCut: boolean }

/**
 * Mirrors printing.split_labels. Horizontal: | inline is a half cut, || a full
 * cut. Vertical: a line of 3 to 5 dashes is a half cut, 6 or more a full cut.
 */
export function splitLabels(text: string, direction: LabelStyle['direction'] = 'horizontal'): Label[] {
  const [pattern, fullLength] =
    direction === 'vertical' ? [/\n?^[ \t]*(-{3,})[ \t]*$\n?/m, 6] : [/[ \t]*(\|+)[ \t]*/, 2]
  const parts = text.split(pattern) // label, mark, label, mark, ..., label
  const labels: Label[] = []
  for (let i = 0; i < parts.length; i += 2) {
    const fullCut = i + 1 === parts.length || parts[i + 1].length >= fullLength
    if (parts[i].trim()) labels.push({ text: parts[i], fullCut })
    else if (labels.length && fullCut) labels[labels.length - 1].fullCut = true
  }
  if (labels.length) labels[labels.length - 1].fullCut = true
  return labels
}

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const response = await fetch('/api/status', { signal })
  if (!response.ok) throw new Error(`status request failed: HTTP ${response.status}`)
  return response.json()
}

export async function printLabel(text: string, style = DEFAULT_STYLE, copies = 1): Promise<PrintResult> {
  const response = await fetch('/api/print', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, copies, style }),
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

/** Vertical labels come back upright (start at the top), so they read in the order typed. */
export function previewUrl(text: string, tapeMm: number, style = DEFAULT_STYLE): string {
  const query = new URLSearchParams({ text, tape: String(tapeMm), style: JSON.stringify(style) })
  if (style.direction === 'vertical') query.set('upright', '1')
  return `/api/preview?${query}`
}

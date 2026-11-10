import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_STYLE, fetchFitSize, fetchStatus, previewUrl, printLabel, splitLabels } from './api'

afterEach(() => vi.unstubAllGlobals())

function respond(body: string, status = 200) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(body, { status }))))
}

describe('api', () => {
  it('encodes multi-line text and tape width in the preview URL', () => {
    const url = new URL(previewUrl('A & B\nC', 12), 'http://x')
    expect(url.pathname).toBe('/api/preview')
    expect(url.searchParams.get('text')).toBe('A & B\nC')
    expect(url.searchParams.get('tape')).toBe('12')
    expect(JSON.parse(url.searchParams.get('style')!)).toEqual(DEFAULT_STYLE)
    expect(url.searchParams.has('upright')).toBe(false)
  })

  it('asks for vertical labels upright', () => {
    const url = new URL(previewUrl('01a', 12, { ...DEFAULT_STYLE, direction: 'vertical' }), 'http://x')
    expect(url.searchParams.get('upright')).toBe('1')
  })

  it('splits labels at lines of three or more dashes, like the server', () => {
    const texts = (text: string) => splitLabels(text, 'vertical').map((label) => label.text)
    expect(texts('---\n01a\n\n\n---\n02a\nline 2\n  -----  \n\n---\n')).toEqual(['01a\n\n', '02a\nline 2'])
    expect(texts('01a\n---\n\n01')).toEqual(['01a', '\n01'])
    expect(texts('a --- b\n--')).toEqual(['a --- b\n--'])
    expect(splitLabels('---\n \n---', 'vertical')).toEqual([])
  })

  it('picks the cut from the number of dashes, like the server', () => {
    expect(splitLabels('01a\n---\n02a\n-----\n03a\n------\n04a\n----------\n05a', 'vertical')).toEqual([
      { text: '01a', fullCut: false },
      { text: '02a', fullCut: false },
      { text: '03a', fullCut: true },
      { text: '04a', fullCut: true },
      { text: '05a', fullCut: true },
    ])
  })

  it('splits horizontal text at inline pipes, like the server', () => {
    expect(splitLabels('01a | 02a||03a |\t04a\nline 2')).toEqual([
      { text: '01a', fullCut: false },
      { text: '02a', fullCut: true },
      { text: '03a', fullCut: false },
      { text: '04a\nline 2', fullCut: true },
    ])
    expect(splitLabels('a\n---\nb')).toHaveLength(1)
    expect(splitLabels('a | b', 'vertical')).toHaveLength(1)
    expect(splitLabels('a || | b')).toEqual([{ text: 'a', fullCut: true }, { text: 'b', fullCut: true }])
    expect(splitLabels('a | | b')).toEqual([{ text: 'a', fullCut: false }, { text: 'b', fullCut: true }])
  })

  it('sends the style with the print request', async () => {
    respond(JSON.stringify({ ok: true, raster_lines: 1, length_mm: 1 }))
    await printLabel('Hi', { ...DEFAULT_STYLE, italic: true }, 3)
    const [, init] = vi.mocked(fetch).mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Hi', copies: 3, style: { ...DEFAULT_STYLE, italic: true } })
  })

  it('fails the status call on an HTTP error', async () => {
    respond('oops', 502)
    await expect(fetchStatus()).rejects.toThrow('HTTP 502')
  })

  it('asks the fit endpoint with the preview query', async () => {
    respond(JSON.stringify({ size_pt: 51.2 }))
    await expect(fetchFitSize(previewUrl('Hi', 12))).resolves.toBe(51.2)
    const [url] = vi.mocked(fetch).mock.calls[0] as unknown as [string]
    expect(new URL(url, 'http://x').pathname).toBe('/api/fit')
  })

  it('fails the fit call on an HTTP error', async () => {
    respond('{}', 400)
    await expect(fetchFitSize(previewUrl('Hi', 12))).rejects.toThrow('HTTP 400')
  })

  it('returns the print result', async () => {
    respond(JSON.stringify({ ok: true, raster_lines: 386, length_mm: 54.5 }))
    await expect(printLabel('Hi')).resolves.toEqual({ ok: true, raster_lines: 386, length_mm: 54.5 })
  })

  it('surfaces the server error message when printing fails', async () => {
    respond(JSON.stringify({ error: 'Printer not ready: cover open' }), 409)
    await expect(printLabel('Hi')).rejects.toThrow('Printer not ready: cover open')
  })

  it('falls back to the HTTP status when the error JSON has no message', async () => {
    respond(JSON.stringify({ detail: 'nope' }), 500)
    await expect(printLabel('Hi')).rejects.toThrow('HTTP 500')
  })

  it('falls back to the HTTP status when the error body is not JSON', async () => {
    respond('<html>Bad Gateway</html>', 502)
    await expect(printLabel('Hi')).rejects.toThrow('HTTP 502')
  })
})

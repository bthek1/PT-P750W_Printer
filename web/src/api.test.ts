import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_STYLE, fetchStatus, previewUrl, printLabel } from './api'

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
  })

  it('sends the style with the print request', async () => {
    respond(JSON.stringify({ ok: true, raster_lines: 1, length_mm: 1 }))
    await printLabel('Hi', false, { ...DEFAULT_STYLE, italic: true })
    const [, init] = vi.mocked(fetch).mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Hi', cut: false, style: { ...DEFAULT_STYLE, italic: true } })
  })

  it('fails the status call on an HTTP error', async () => {
    respond('oops', 502)
    await expect(fetchStatus()).rejects.toThrow('HTTP 502')
  })

  it('returns the print result', async () => {
    respond(JSON.stringify({ ok: true, raster_lines: 386, length_mm: 54.5 }))
    await expect(printLabel('Hi', true)).resolves.toEqual({ ok: true, raster_lines: 386, length_mm: 54.5 })
  })

  it('surfaces the server error message when printing fails', async () => {
    respond(JSON.stringify({ error: 'Printer not ready: cover open' }), 409)
    await expect(printLabel('Hi', true)).rejects.toThrow('Printer not ready: cover open')
  })

  it('falls back to the HTTP status when the error JSON has no message', async () => {
    respond(JSON.stringify({ detail: 'nope' }), 500)
    await expect(printLabel('Hi', true)).rejects.toThrow('HTTP 500')
  })

  it('falls back to the HTTP status when the error body is not JSON', async () => {
    respond('<html>Bad Gateway</html>', 502)
    await expect(printLabel('Hi', true)).rejects.toThrow('HTTP 502')
  })
})

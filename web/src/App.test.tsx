import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { DEFAULT_STYLE, type StatusResponse } from './api'

const READY: StatusResponse = {
  connected: true,
  busy: false,
  device: '/dev/usb/lp3',
  error: null,
  checked_at: '2026-11-10T08:00:00+00:00',
  status: {
    model_code: 0x68,
    status_type: 'reply to status request',
    errors: [],
    tape_mm: 24,
    media: 'laminated',
    tape_colour: 'white',
    text_colour: 'black',
    raw: '',
  },
}

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }))
}

function mockServer(status: StatusResponse | 'down', print = json({ ok: true, raster_lines: 386, length_mm: 54.5 })) {
  const fetchMock = vi.fn((url: string) => {
    if (url === '/api/status') return status === 'down' ? Promise.reject(new TypeError('fetch failed')) : json(status)
    if (url === '/api/print') return print
    if (url.startsWith('/api/fit?')) return json({ size_pt: 51.2 })
    return json({ error: 'unexpected' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('shows live status from the server', async () => {
    mockServer(READY)
    render(<App />)
    expect(await screen.findByRole('status')).toHaveTextContent('Ready')
    expect(screen.getByRole('region', { name: 'Printer status' })).toHaveTextContent('24 mm')
    expect(screen.getByText('black on white')).toBeInTheDocument()
  })

  it('reports a missing printer and disables printing', async () => {
    mockServer({ ...READY, connected: false, status: null, error: 'No PT-P750W (04f9:2062) found.' })
    render(<App />)
    expect(await screen.findByText('Printer not found')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    expect(screen.getByRole('button', { name: 'Print' })).toBeDisabled()
  })

  it('reports an offline server', async () => {
    mockServer('down')
    render(<App />)
    expect(await screen.findByText('Server offline')).toBeInTheDocument()
  })

  it('shows printer errors', async () => {
    mockServer({ ...READY, status: { ...READY.status!, errors: ['cover open'] } })
    render(<App />)
    expect(await screen.findByText('cover open')).toBeInTheDocument()
  })

  it('prints the typed text', async () => {
    const fetchMock = mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hello\nWorld' } })
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    expect(await screen.findByText(/Printed/)).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/print') as unknown as [string, RequestInit]
    expect(JSON.parse(call[1].body as string)).toEqual({ text: 'Hello\nWorld', cut: true, style: DEFAULT_STYLE })
  })

  it('shows the server error when printing fails', async () => {
    mockServer(READY, json({ error: 'Printer not ready: cover open' }, 409))
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    await waitFor(() => expect(screen.getByText('Printer not ready: cover open')).toBeInTheDocument())
  })

  it('previews the label at the loaded tape width', async () => {
    mockServer({ ...READY, status: { ...READY.status!, tape_mm: 12 } })
    render(<App />)
    await screen.findByText('Ready')
    expect(screen.getByText('Preview appears here')).toBeInTheDocument()
    const side = screen.getByRole('img', { name: 'Tape width ruler, 0 to 12 mm' })
    expect(side).toHaveTextContent('0')
    expect(side).toHaveTextContent('12 mm')
    // Half the height of 24 mm tape (170 px), at 1 px per 180 dpi dot.
    expect(screen.getByTestId('tape')).toHaveStyle({ height: '85px' })
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi\nthere' } })
    const preview = await screen.findByAltText('Label preview')
    const url = new URL(preview.getAttribute('src')!, 'http://x')
    expect(url.searchParams.get('text')).toBe('Hi\nthere')
    expect(url.searchParams.get('tape')).toBe('12')
  })

  it('prints with Ctrl+Enter', async () => {
    const fetchMock = mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    const editor = screen.getByLabelText(/Label text/)
    fireEvent.change(editor, { target: { value: 'Hi' } })
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(await screen.findByText(/Printed/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/print')).toHaveLength(1)
  })

  it('sends cut: false when the checkbox is cleared', async () => {
    const fetchMock = mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Cut after printing' }))
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    await screen.findByText(/Printed/)
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/print') as unknown as [string, RequestInit]
    expect(JSON.parse(call[1].body as string)).toEqual({ text: 'Hi', cut: false, style: DEFAULT_STYLE })
  })

  it('disables Print for blank text', async () => {
    mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: '  \n ' } })
    expect(screen.getByRole('button', { name: 'Print' })).toBeDisabled()
  })

  it('disables Print while another job is printing', async () => {
    mockServer({ ...READY, busy: true })
    render(<App />)
    expect(await screen.findByRole('status')).toHaveTextContent('Printing')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    expect(screen.getByRole('button', { name: 'Print' })).toBeDisabled()
  })

  it('blocks a second print while the first is running', async () => {
    let finish: (r: Response) => void = () => {}
    const fetchMock = mockServer(READY, new Promise<Response>((r) => (finish = r)))
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    const button = await screen.findByRole('button', { name: 'Printing...' })
    expect(button).toBeDisabled()
    fireEvent.keyDown(screen.getByLabelText(/Label text/), { key: 'Enter', ctrlKey: true })
    finish(new Response(JSON.stringify({ ok: true, raster_lines: 1, length_mm: 0.1 })))
    await screen.findByText(/Printed/)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/print')).toHaveLength(1)
  })

  it('sends the chosen formatting with preview and print', async () => {
    const fetchMock = mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    fireEvent.change(screen.getByLabelText('Font'), { target: { value: 'serif' } })
    fireEvent.change(screen.getByLabelText('Font size'), { target: { value: '12' } })
    fireEvent.change(screen.getByLabelText('Case'), { target: { value: 'upper' } })
    fireEvent.change(screen.getByLabelText('Line spacing'), { target: { value: '1.5' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Align left' }))
    fireEvent.click(screen.getByRole('button', { name: /Italic/ }))
    fireEvent.click(screen.getByRole('button', { name: /Bold/ }))
    fireEvent.click(screen.getByRole('button', { name: 'White on black' }))
    fireEvent.click(screen.getByRole('button', { name: 'Border' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Vertical' }))
    const expected = {
      ...DEFAULT_STYLE,
      font: 'serif',
      size_pt: 12,
      case: 'upper',
      line_spacing: 1.5,
      align: 'left',
      italic: true,
      bold: false,
      invert: true,
      border: true,
      direction: 'vertical',
    }
    await waitFor(() => {
      const src = screen.getByAltText('Label preview').getAttribute('src')!
      expect(JSON.parse(new URL(src, 'http://x').searchParams.get('style')!)).toEqual(expected)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    await screen.findByText(/Printed/)
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/print') as unknown as [string, RequestInit]
    expect(JSON.parse(call[1].body as string).style).toEqual(expected)
  })

  it('toggles bold, italic and underline with Ctrl shortcuts', async () => {
    mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    const editor = screen.getByLabelText(/Label text/)
    fireEvent.keyDown(editor, { key: 'b', ctrlKey: true })
    fireEvent.keyDown(editor, { key: 'I', ctrlKey: true })
    fireEvent.keyDown(editor, { key: 'u', metaKey: true })
    fireEvent.keyDown(editor, { key: 'u', ctrlKey: true, shiftKey: true })
    fireEvent.keyDown(editor, { key: 'x', ctrlKey: true })
    fireEvent.keyDown(editor, { key: 'b' })
    expect(screen.getByRole('button', { name: /Bold/ })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: /Italic/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /Underline/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('clears formatting back to the defaults', async () => {
    mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    const clear = screen.getByRole('button', { name: 'Clear formatting' })
    expect(clear).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Outline' }))
    fireEvent.change(screen.getByLabelText('Font size'), { target: { value: '24' } })
    fireEvent.change(screen.getByLabelText('Font size'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Align left' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Align left' }))
    expect(screen.getByRole('radio', { name: 'Align left' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(clear)
    expect(screen.getByRole('button', { name: 'Outline' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('radio', { name: 'Centre' })).toHaveAttribute('aria-checked', 'true')
    expect(clear).toBeDisabled()
  })

  it('shows the label length once the preview loads', async () => {
    mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    const preview = await screen.findByAltText('Label preview')
    // 709 raster lines at 180 dpi is 100 mm.
    Object.defineProperty(preview, 'naturalWidth', { value: 709 })
    fireEvent.load(preview)
    expect(await screen.findByText('About 100 mm of text on 24 mm tape, plus feed margins.')).toBeInTheDocument()
    // The ruler runs past the label; the marker shows where the label ends.
    const length = screen.getByRole('img', { name: 'Length ruler, label 100.0 mm' })
    expect(Number(length.getAttribute('width'))).toBeGreaterThan(709)
    expect(screen.getByTestId('ruler-marker')).toHaveTextContent('100 mm')
  })

  it('shows the fitted size on the auto size option', async () => {
    const fetchMock = mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    expect(screen.getByRole('option', { name: 'Auto size' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    expect(await screen.findByRole('option', { name: '51 pt (max)' })).toBeInTheDocument()
    const fit = new URL(fetchMock.mock.calls.map(([url]) => url).find((url) => url.startsWith('/api/fit?'))!, 'http://x')
    expect(fit.searchParams.get('text')).toBe('Hi')
    expect(fit.searchParams.get('tape')).toBe('24')
    // Clearing the text drops the stale number.
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: '' } })
    expect(await screen.findByRole('option', { name: 'Auto size' })).toBeInTheDocument()
  })

  it('updates the preview when only the style changes', async () => {
    mockServer(READY)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    const before = (await screen.findByAltText('Label preview')).getAttribute('src')
    fireEvent.click(screen.getByRole('button', { name: 'Border' }))
    await waitFor(() => expect(screen.getByAltText('Label preview').getAttribute('src')).not.toBe(before))
  })

  it('shows a missing printer without an error detail', async () => {
    mockServer({ ...READY, connected: false, status: null, error: null })
    render(<App />)
    expect(await screen.findByText('Printer not found')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows a print failure that is not an Error object', async () => {
    const reset = Promise.reject('connection reset')
    // Created before the app awaits it; mark it handled so Vitest does not flag it.
    reset.catch(() => {})
    mockServer(READY, reset)
    render(<App />)
    await screen.findByText('Ready')
    fireEvent.change(screen.getByLabelText(/Label text/), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Print' }))
    expect(await screen.findByText('connection reset')).toBeInTheDocument()
  })
})

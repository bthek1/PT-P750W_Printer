import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { StatusResponse } from './api'

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
    expect(screen.getByText('24 mm')).toBeInTheDocument()
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
    expect(JSON.parse(call[1].body as string)).toEqual({ text: 'Hello\nWorld', cut: true })
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
    expect(JSON.parse(call[1].body as string)).toEqual({ text: 'Hi', cut: false })
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
})

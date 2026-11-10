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
})

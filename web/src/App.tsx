import { useEffect, useState, type KeyboardEvent } from 'react'
import { previewUrl, printLabel } from './api'
import { usePrinterStatus, type Connection } from './usePrinterStatus'

const DEFAULT_TAPE_MM = 24
const MAX_TEXT = 200
const DOTS_PER_MM = 180 / 25.4

type Badge = { label: string; detail?: string; tone: 'grey' | 'green' | 'amber' | 'red' | 'blue' }

const TONES: Record<Badge['tone'], string> = {
  grey: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200',
  green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200',
  amber: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  red: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  blue: 'bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200',
}

function badgeFor(connection: Connection): Badge {
  if (connection.state === 'connecting') return { label: 'Connecting', tone: 'grey' }
  if (connection.state === 'offline')
    return { label: 'Server offline', detail: 'Start it with `just serve`. Retrying every 2 s.', tone: 'red' }
  const { data } = connection
  if (data.busy) return { label: 'Printing', tone: 'blue' }
  if (!data.connected) return { label: 'Printer not found', detail: data.error ?? undefined, tone: 'amber' }
  if (data.status?.errors.length) return { label: 'Printer error', detail: data.status.errors.join(', '), tone: 'red' }
  return { label: 'Ready', tone: 'green' }
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

type PrintState =
  | { state: 'idle' }
  | { state: 'printing' }
  | { state: 'done'; lengthMm: number }
  | { state: 'failed'; error: string }

export default function App() {
  const [connection, refresh] = usePrinterStatus()
  const [text, setText] = useState('')
  const [cut, setCut] = useState(true)
  const [job, setJob] = useState<PrintState>({ state: 'idle' })
  const [previewWidth, setPreviewWidth] = useState<number | null>(null)

  const data = connection.state === 'online' ? connection.data : null
  const info = data?.status ?? null
  const tapeMm = info?.tape_mm ?? DEFAULT_TAPE_MM
  const badge = badgeFor(connection)
  const ready = badge.tone === 'green'
  const previewText = useDebounced(text.trim() ? text : '', 300)
  const canPrint = ready && text.trim() !== '' && job.state !== 'printing'

  async function print() {
    if (!canPrint) return
    setJob({ state: 'printing' })
    try {
      const result = await printLabel(text, cut)
      setJob({ state: 'done', lengthMm: result.length_mm })
    } catch (error) {
      setJob({ state: 'failed', error: error instanceof Error ? error.message : String(error) })
    }
    refresh()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      print()
    }
  }

  return (
    <main className="min-h-screen bg-zinc-50 px-4 py-8 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="mx-auto flex max-w-2xl flex-col gap-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold">PT-P750W Labels</h1>
          <span role="status" className={`rounded-full px-3 py-1 text-sm font-medium ${TONES[badge.tone]}`}>
            {badge.label}
          </span>
        </header>

        {badge.detail && <p className="text-sm text-zinc-600 dark:text-zinc-400">{badge.detail}</p>}

        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Printer status">
          <Stat label="Tape" value={info ? `${info.tape_mm} mm` : 'unknown'} />
          <Stat label="Media" value={info?.media ?? 'unknown'} />
          <Stat label="Colours" value={info ? `${info.text_colour} on ${info.tape_colour}` : 'unknown'} />
          <Stat
            label="Last check"
            value={data?.checked_at ? new Date(data.checked_at).toLocaleTimeString() : 'never'}
          />
        </section>

        <section className="flex flex-col gap-3">
          <label htmlFor="label-text" className="text-sm font-medium">
            Label text <span className="font-normal text-zinc-500">(new line = second line on the tape)</span>
          </label>
          <textarea
            id="label-text"
            value={text}
            maxLength={MAX_TEXT}
            rows={3}
            autoFocus
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type label text"
            className="resize-y rounded-lg border border-zinc-300 bg-white p-3 font-sans text-lg outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-200 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-sky-900"
          />

          <div className="overflow-x-auto rounded-lg border border-dashed border-zinc-300 bg-white p-3 dark:border-zinc-700">
            {previewText ? (
              <img
                src={previewUrl(previewText, tapeMm)}
                alt="Label preview"
                className="h-16 max-w-none [image-rendering:pixelated]"
                onLoad={(event) => setPreviewWidth(event.currentTarget.naturalWidth)}
              />
            ) : (
              <p className="py-5 text-center text-sm text-zinc-400">Preview appears here</p>
            )}
          </div>
          {previewText && previewWidth && (
            <p className="text-xs text-zinc-500">
              About {(previewWidth / DOTS_PER_MM).toFixed(0)} mm of text on {tapeMm} mm tape, plus feed margins.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={print}
              disabled={!canPrint}
              className="rounded-lg bg-sky-600 px-5 py-2.5 font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:text-zinc-500 dark:disabled:bg-zinc-800"
            >
              {job.state === 'printing' ? 'Printing...' : 'Print'}
            </button>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={cut} onChange={(event) => setCut(event.target.checked)} />
              Cut after printing
            </label>
            <span className="text-xs text-zinc-500">Ctrl+Enter prints</span>
          </div>

          {job.state === 'done' && (
            <p className="text-sm text-emerald-700 dark:text-emerald-400">
              Printed ({job.lengthMm} mm of text).
            </p>
          )}
          {job.state === 'failed' && <p className="text-sm text-red-700 dark:text-red-400">{job.error}</p>}
        </section>
      </div>
    </main>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 font-medium">{value}</div>
    </div>
  )
}

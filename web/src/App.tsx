import { useEffect, useState, type KeyboardEvent } from 'react'
import { AlertCircle, CheckCircle2, Loader2, Printer, Tag } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { previewUrl, printLabel } from './api'
import { usePrinterStatus, type Connection } from './usePrinterStatus'

const DEFAULT_TAPE_MM = 24
const MAX_TEXT = 200
const DOTS_PER_MM = 180 / 25.4

type Tone = 'ready' | 'busy' | 'waiting' | 'error'
type StatusBadge = { label: string; detail?: string; tone: Tone }

const DOTS: Record<Tone, string> = {
  ready: 'bg-emerald-500',
  busy: 'bg-sky-500',
  waiting: 'bg-amber-500',
  error: 'bg-destructive',
}

function badgeFor(connection: Connection): StatusBadge {
  if (connection.state === 'connecting') return { label: 'Connecting', tone: 'waiting' }
  if (connection.state === 'offline')
    return { label: 'Server offline', detail: 'Start it with `just serve`. Retrying every 2 s.', tone: 'error' }
  const { data } = connection
  if (data.busy) return { label: 'Printing', tone: 'busy' }
  if (!data.connected) return { label: 'Printer not found', detail: data.error ?? undefined, tone: 'waiting' }
  if (data.status?.errors.length) return { label: 'Printer error', detail: data.status.errors.join(', '), tone: 'error' }
  return { label: 'Ready', tone: 'ready' }
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
  const previewText = useDebounced(text.trim() ? text : '', 300)
  const canPrint = badge.tone === 'ready' && text.trim() !== '' && job.state !== 'printing'

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
    <main className="min-h-screen bg-muted/40 px-4 py-8">
      <div className="mx-auto flex max-w-2xl flex-col gap-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Tag className="size-6" aria-hidden />
            PT-P750W Labels
          </h1>
          <Badge role="status" variant={badge.tone === 'error' ? 'destructive' : 'outline'} className="gap-2 px-3 py-1">
            {badge.tone === 'busy' ? (
              <Loader2 className="size-3 animate-spin" aria-hidden />
            ) : (
              <span className={`size-2 rounded-full ${DOTS[badge.tone]}`} aria-hidden />
            )}
            {badge.label}
          </Badge>
        </header>

        {badge.detail && (
          <Alert variant={badge.tone === 'error' ? 'destructive' : 'default'}>
            <AlertCircle aria-hidden />
            <AlertDescription>{badge.detail}</AlertDescription>
          </Alert>
        )}

        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Printer status">
          <Stat label="Tape" value={info ? `${info.tape_mm} mm` : 'unknown'} />
          <Stat label="Media" value={info?.media ?? 'unknown'} />
          <Stat label="Colours" value={info ? `${info.text_colour} on ${info.tape_colour}` : 'unknown'} />
          <Stat
            label="Last check"
            value={data?.checked_at ? new Date(data.checked_at).toLocaleTimeString() : 'never'}
          />
        </section>

        <Card>
          <CardHeader>
            <CardTitle>New label</CardTitle>
            <CardDescription>A new line prints as a second line on the tape. Ctrl+Enter prints.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="label-text">Label text</Label>
              <Textarea
                id="label-text"
                value={text}
                maxLength={MAX_TEXT}
                rows={3}
                autoFocus
                onChange={(event) => setText(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Type label text"
                className="text-lg"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label>Preview</Label>
              <div className="overflow-x-auto rounded-md border border-dashed bg-white p-3">
                {previewText ? (
                  <img
                    src={previewUrl(previewText, tapeMm)}
                    alt="Label preview"
                    className="h-16 max-w-none [image-rendering:pixelated]"
                    onLoad={(event) => setPreviewWidth(event.currentTarget.naturalWidth)}
                  />
                ) : (
                  <p className="py-5 text-center text-sm text-neutral-400">Preview appears here</p>
                )}
              </div>
              {previewText && previewWidth && (
                <p className="text-xs text-muted-foreground">
                  About {(previewWidth / DOTS_PER_MM).toFixed(0)} mm of text on {tapeMm} mm tape, plus feed margins.
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <Button onClick={print} disabled={!canPrint} size="lg">
                {job.state === 'printing' ? <Loader2 className="animate-spin" aria-hidden /> : <Printer aria-hidden />}
                {job.state === 'printing' ? 'Printing...' : 'Print'}
              </Button>
              <div className="flex items-center gap-2">
                <Checkbox id="cut" checked={cut} onCheckedChange={(checked) => setCut(checked === true)} />
                <Label htmlFor="cut">Cut after printing</Label>
              </div>
            </div>

            {job.state === 'done' && (
              <Alert>
                <CheckCircle2 aria-hidden className="text-emerald-600" />
                <AlertTitle>Printed ({job.lengthMm} mm of text).</AlertTitle>
              </Alert>
            )}
            {job.state === 'failed' && (
              <Alert variant="destructive">
                <AlertCircle aria-hidden />
                <AlertTitle>Print failed</AlertTitle>
                <AlertDescription>{job.error}</AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="mt-1 font-medium">{value}</div>
      </CardContent>
    </Card>
  )
}

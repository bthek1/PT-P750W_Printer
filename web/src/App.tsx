import { useEffect, useState, type KeyboardEvent } from 'react'
import { AlertCircle, CheckCircle2, Loader2, Printer, Tag } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  DEFAULT_STYLE,
  fetchFitSize,
  MAX_COPIES,
  MAX_PAGES,
  previewUrl,
  printLabel,
  splitLabels,
  toggleFlag,
  type Flag,
  type LabelStyle,
} from './api'
import { FormatToolbar } from './FormatToolbar'
import { Ruler } from './Ruler'
import { usePrinterStatus, type Connection } from './usePrinterStatus'

const DEFAULT_TAPE_MM = 24
const MAX_TEXT = 1000
const DOTS_PER_MM = 180 / 25.4
// The length ruler is clipped to the preview width, so this only has to exceed the widest screen.
const RULER_SLACK_MM = 300
// Browsers keep Ctrl+L/E/R (address bar, search, reload), so only these are bound.
const SHORTCUTS: Record<string, Flag> = { b: 'bold', i: 'italic', u: 'underline' }
const CUT_MARKS = {
  horizontal: (
    <>
      Type <code>|</code> between labels for a half cut (they stay on the same backing) or <code>||</code> for a full
      cut, for example <code>01a | 02a || 03a</code>.
    </>
  ),
  vertical: (
    <>
      A line of <code>---</code> starts the next label with a half cut (it stays on the same backing), a line of{' '}
      <code>------</code> with a full cut.
    </>
  ),
}

function clampCopies(value: number): number {
  return Number.isNaN(value) ? 1 : Math.min(MAX_COPIES, Math.max(1, Math.round(value)))
}

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

/** Size the renderer picks for the auto option. Keeps the last value while the next one loads. */
function useAutoSize(preview: string): number | null {
  const [size, setSize] = useState<number | null>(null)
  useEffect(() => {
    if (!preview) return
    const controller = new AbortController()
    fetchFitSize(preview, controller.signal).then(setSize, () => {})
    return () => controller.abort()
  }, [preview])
  return preview ? size : null
}

type PrintState =
  | { state: 'idle' }
  | { state: 'printing' }
  | { state: 'done'; lengthMm: number; labels: number }
  | { state: 'failed'; error: string }

export default function App() {
  const [connection, refresh] = usePrinterStatus()
  const [text, setText] = useState('')
  const [copies, setCopies] = useState(1)
  const [style, setStyle] = useState<LabelStyle>(DEFAULT_STYLE)
  const [job, setJob] = useState<PrintState>({ state: 'idle' })
  // Label length in dots: the image width, or its height when shown upright.
  const [previewLength, setPreviewLength] = useState<number | null>(null)

  const data = connection.state === 'online' ? connection.data : null
  const info = data?.status ?? null
  const tapeMm = info?.tape_mm ?? DEFAULT_TAPE_MM
  const tapePx = Math.round(tapeMm * DOTS_PER_MM)
  const badge = badgeFor(connection)
  const preview = useDebounced(text.trim() ? previewUrl(text, tapeMm, style) : '', 300)
  const upright = style.direction === 'vertical'
  const labelMm = preview && previewLength ? Math.round((previewLength / DOTS_PER_MM) * 10) / 10 : null
  const autoSizePt = useAutoSize(style.size_pt === null ? preview : '')
  const labels = splitLabels(text, style.direction)
  const labelCount = labels.length
  const strips = labels.filter((label) => label.fullCut).length
  const pages = labelCount * copies
  const canPrint = badge.tone === 'ready' && labelCount > 0 && pages <= MAX_PAGES && job.state !== 'printing'

  async function print() {
    if (!canPrint) return
    setJob({ state: 'printing' })
    try {
      const result = await printLabel(text, style, copies)
      setJob({ state: 'done', lengthMm: result.length_mm, labels: result.labels })
    } catch (error) {
      setJob({ state: 'failed', error: error instanceof Error ? error.message : String(error) })
    }
    refresh()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!(event.ctrlKey || event.metaKey)) return
    const flag = SHORTCUTS[event.key.toLowerCase()]
    if (event.key === 'Enter') {
      event.preventDefault()
      print()
    } else if (flag && !event.shiftKey && !event.altKey) {
      event.preventDefault()
      setStyle((current) => toggleFlag(current, flag))
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
            <CardDescription>
              A new line prints as a second line on the tape. {CUT_MARKS[style.direction]} The last label always gets
              a full cut. Formatting applies to every label. Ctrl+Enter prints.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="label-text">Label text</Label>
              <Textarea
                id="label-text"
                value={text}
                maxLength={MAX_TEXT}
                rows={labelCount > 1 ? 8 : 3}
                autoFocus
                onChange={(event) => setText(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Type label text"
                className="text-lg"
              />
            </div>

            <FormatToolbar style={style} onChange={setStyle} autoSizePt={autoSizePt} />

            <div className="flex flex-col gap-2">
              <Label>Preview</Label>
              {upright ? (
                <UprightPreview
                  preview={preview}
                  tapeMm={tapeMm}
                  tapePx={tapePx}
                  labelMm={labelMm}
                  onLength={setPreviewLength}
                />
              ) : (
                <div className="flex items-start gap-2 rounded-md border border-dashed p-3">
                  <Ruler
                    orientation="vertical"
                    mm={tapeMm}
                    pxPerMm={DOTS_PER_MM}
                    label={`Tape width ruler, 0 to ${tapeMm} mm`}
                  />
                  <div className="min-w-0 flex-1 overflow-x-auto pb-1">
                    <div className="w-max min-w-full">
                      {/* 1 CSS px per printer dot: the tape is drawn to scale and the bitmap at a whole-number scale. */}
                      <div
                        data-testid="tape"
                        className="flex w-max min-w-24 items-center justify-center bg-white shadow-sm ring-1 ring-border"
                        style={{ height: tapePx }}
                      >
                        {preview ? (
                          <img
                            src={preview}
                            alt="Label preview"
                            className="max-w-none [image-rendering:pixelated]"
                            onLoad={(event) => setPreviewLength(event.currentTarget.naturalWidth)}
                          />
                        ) : (
                          <p className="px-4 text-center text-sm text-neutral-400">Preview appears here</p>
                        )}
                      </div>
                      {/* Absolute, so the long ruler fills the preview width without widening the scroll area.
                          overflow-clip, not hidden: a hidden box can still be scrolled (find-in-page, scrollIntoView). */}
                      <div className="relative h-[22px] overflow-clip">
                        <div className="absolute top-0 left-0">
                          <Ruler
                            orientation="horizontal"
                            mm={(labelMm ?? 0) + RULER_SLACK_MM}
                            pxPerMm={DOTS_PER_MM}
                            marker={labelMm ?? undefined}
                            label={labelMm === null ? 'Length ruler' : `Length ruler, label ${labelMm.toFixed(1)} mm`}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {preview && previewLength && (
                <p className="text-xs text-muted-foreground">
                  {labelCount > 1
                    ? `${labelCount} labels on ${strips} ${strips > 1 ? 'strips' : 'strip'}, in one font size on ${tapeMm} mm tape. Dashed line: half cut. Solid line: full cut. Gaps are approximate.`
                    : `About ${(previewLength / DOTS_PER_MM).toFixed(0)} mm of text on ${tapeMm} mm tape, plus feed margins.`}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <Button onClick={print} disabled={!canPrint} size="lg">
                {job.state === 'printing' ? <Loader2 className="animate-spin" aria-hidden /> : <Printer aria-hidden />}
                {job.state === 'printing' ? 'Printing...' : pages > 1 ? `Print ${pages} labels` : 'Print'}
              </Button>
              <div className="flex items-center gap-2">
                <Label htmlFor="copies">Copies</Label>
                <input id="copies" type="number" min={1} max={MAX_COPIES} value={copies}
                  className="h-8 w-16 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
                  onChange={(event) => setCopies(clampCopies(event.target.valueAsNumber))} />
              </div>
            </div>
            {pages > MAX_PAGES && (
              <p role="alert" className="text-sm text-destructive">
                {labelCount} labels x {copies} copies is more than {MAX_PAGES} labels per job.
              </p>
            )}

            {job.state === 'done' && (
              <Alert>
                <CheckCircle2 aria-hidden className="text-emerald-600" />
                <AlertTitle>
                  {job.labels > 1 ? `Printed ${job.labels} labels.` : `Printed (${job.lengthMm} mm of text).`}
                </AlertTitle>
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

type UprightProps = {
  preview: string
  tapeMm: number
  tapePx: number
  labelMm: number | null
  onLength: (dots: number) => void
}

/** Vertical labels, turned so the start of the strip is at the top: they read in the order typed. */
function UprightPreview({ preview, tapeMm, tapePx, labelMm, onLength }: UprightProps) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-dashed p-3">
      <p className="text-xs text-muted-foreground">Start of the strip at the top. It prints in this order.</p>
      <div className="pl-[48px]">
        <Ruler orientation="horizontal" mm={tapeMm} pxPerMm={DOTS_PER_MM} label={`Tape width ruler, 0 to ${tapeMm} mm`} />
      </div>
      <div className="flex items-start gap-2">
        {labelMm === null ? (
          <div className="w-10 shrink-0" />
        ) : (
          <Ruler orientation="vertical" mm={labelMm} pxPerMm={DOTS_PER_MM} label={`Length ruler, label ${labelMm.toFixed(1)} mm`} />
        )}
        {/* 1 CSS px per printer dot, as in the horizontal view. */}
        <div
          data-testid="tape"
          className="flex min-h-24 shrink-0 items-start justify-center bg-white shadow-sm ring-1 ring-border"
          style={{ width: tapePx }}
        >
          {preview ? (
            <img
              src={preview}
              alt="Label preview"
              className="max-w-none [image-rendering:pixelated]"
              onLoad={(event) => onLength(event.currentTarget.naturalHeight)}
            />
          ) : (
            <p className="py-4 text-center text-sm text-neutral-400">Preview appears here</p>
          )}
        </div>
      </div>
    </div>
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

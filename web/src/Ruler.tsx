const THICKNESS = 40
const HORIZONTAL_HEIGHT = 22

type Props = {
  /** Length to cover, in mm. */
  mm: number
  pxPerMm: number
  orientation: 'vertical' | 'horizontal'
  label: string
  /** Horizontal only: mark this length (the label) instead of numbering the end of the ruler. */
  marker?: number
}

function tickLength(mm: number): number {
  if (mm % 10 === 0) return 10
  if (mm % 5 === 0) return 7
  return 4
}

/** Numbers at 0, the end, and every `step` mm that is not crowding the end label. */
function labelled(mm: number, step: number, clearance: number): number[] {
  const marks = [0]
  for (let at = step; at < mm - clearance; at += step) marks.push(at)
  marks.push(mm)
  return marks
}

function formatMm(value: number): string {
  return `${Number.isInteger(value) ? value : value.toFixed(1)} mm`
}

/** A mm ruler drawn to the same scale as the tape preview. Vertical runs 0 at the top. */
export function Ruler({ mm, pxPerMm, orientation, label, marker }: Props) {
  const vertical = orientation === 'vertical'
  const span = Math.round(mm * pxPerMm)
  const whole = Math.floor(mm)
  const ticks = Array.from({ length: whole + 1 }, (_, i) => i)
  if (mm !== whole) ticks.push(mm)
  // The horizontal end label ("23.5 mm") is wider than a vertical one, so it needs more room.
  // With a marker the ruler is open-ended: its end gets no number, and numbers under the
  // right-aligned marker text ("23.5 mm" is about 6 mm wide) are dropped.
  const marks =
    marker === undefined
      ? labelled(mm, 5, vertical ? 3 : 6)
      : Array.from({ length: Math.floor(mm / 5) + 1 }, (_, i) => i * 5).filter(
          (value) => value < marker - 6 || value > marker + 2,
        )
  // Keep the end ticks inside the drawing so their 1 px line is not clipped.
  const at = (value: number) => Math.min(Math.max(value * pxPerMm, 0.5), span - 0.5)

  return (
    <svg
      role="img"
      aria-label={label}
      width={vertical ? THICKNESS : span}
      height={vertical ? span : HORIZONTAL_HEIGHT}
      overflow="visible"
      className="shrink-0 text-muted-foreground"
    >
      {ticks.map((value) => {
        const pos = at(value)
        const len = value === mm ? 10 : tickLength(value)
        return vertical ? (
          <line key={value} x1={THICKNESS - len} x2={THICKNESS} y1={pos} y2={pos} stroke="currentColor" />
        ) : (
          <line key={value} x1={pos} x2={pos} y1={0} y2={len} stroke="currentColor" />
        )
      })}
      {marks.map((value, i) => {
        const pos = at(value)
        const last = i === marks.length - 1
        const text = last && marker === undefined ? formatMm(value) : String(value)
        return vertical ? (
          <text
            key={value}
            x={THICKNESS - 13}
            y={pos}
            textAnchor="end"
            dominantBaseline={i === 0 ? 'hanging' : last ? 'auto' : 'middle'}
            className="fill-current text-[10px] tabular-nums"
          >
            {text}
          </text>
        ) : (
          <text
            key={value}
            x={pos}
            y={HORIZONTAL_HEIGHT - 1}
            textAnchor={value === 0 ? 'start' : last && marker === undefined ? 'end' : 'middle'}
            className="fill-current text-[10px] tabular-nums"
          >
            {text}
          </text>
        )
      })}
      {marker !== undefined && (
        <g data-testid="ruler-marker" className="text-primary">
          <line x1={0} x2={at(marker)} y1={1} y2={1} stroke="currentColor" strokeWidth={2} />
          <line x1={at(marker)} x2={at(marker)} y1={0} y2={HORIZONTAL_HEIGHT} stroke="currentColor" strokeWidth={2} />
          <text
            x={at(marker) - 3}
            y={HORIZONTAL_HEIGHT - 1}
            textAnchor="end"
            className="fill-current text-[10px] font-medium tabular-nums"
          >
            {formatMm(marker)}
          </text>
        </g>
      )}
    </svg>
  )
}

import type { ReactNode } from 'react'
import {
  Bold,
  Italic,
  RemoveFormatting,
  Strikethrough,
  TextAlignCenter,
  TextAlignEnd,
  TextAlignStart,
  Underline,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { DEFAULT_STYLE, type Flag, type LabelStyle } from './api'

// Whole-label formatting. The printer prints one colour and the label is one
// image, so per-character options (subscript, highlight, colour) do not apply.

const FONTS: [LabelStyle['font'], string][] = [
  ['sans', 'Sans'],
  ['condensed', 'Sans Condensed'],
  ['serif', 'Serif'],
  ['mono', 'Mono'],
]
const SIZES = [6, 8, 10, 12, 14, 18, 24, 36, 48, 72]
const CASES: [LabelStyle['case'], string][] = [
  ['none', 'As typed'],
  ['upper', 'UPPERCASE'],
  ['lower', 'lowercase'],
  ['title', 'Capitalize Each Word'],
]
const SPACINGS: LabelStyle['line_spacing'][] = [1, 1.5, 2]

const CHARACTER: Flag[] = ['bold', 'italic', 'underline', 'strike']
const EFFECTS: Flag[] = ['outline', 'invert', 'border']

const SELECT =
  'h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&_option]:bg-popover [&_option]:text-popover-foreground'

function flagsFrom(style: LabelStyle, flags: Flag[]): string[] {
  return flags.filter((flag) => style[flag])
}

function withFlags(style: LabelStyle, flags: Flag[], on: string[]): LabelStyle {
  return { ...style, ...Object.fromEntries(flags.map((flag) => [flag, on.includes(flag)])) }
}

type Props = {
  style: LabelStyle
  onChange: (style: LabelStyle) => void
  /** The size the renderer picked to fit the tape, shown on the auto option. Null until known. */
  autoSizePt?: number | null
}

export function FormatToolbar({ style, onChange, autoSizePt = null }: Props) {
  const set = <K extends keyof LabelStyle>(key: K, value: LabelStyle[K]) => onChange({ ...style, [key]: value })

  return (
    <div role="toolbar" aria-label="Formatting" className="flex flex-wrap items-center gap-2">
      <select aria-label="Font" className={SELECT} value={style.font}
        onChange={(event) => set('font', event.target.value as LabelStyle['font'])}>
        {FONTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <select aria-label="Font size" className={SELECT} value={style.size_pt ?? ''}
        onChange={(event) => set('size_pt', event.target.value ? Number(event.target.value) : null)}>
        <option value="">{autoSizePt === null ? 'Auto size' : `${Math.round(autoSizePt)} pt (max)`}</option>
        {SIZES.map((size) => <option key={size} value={size}>{size} pt</option>)}
      </select>

      <ToggleGroup type="multiple" variant="outline" size="sm" spacing={0} aria-label="Character formatting"
        value={flagsFrom(style, CHARACTER)} onValueChange={(on) => onChange(withFlags(style, CHARACTER, on))}>
        <Item value="bold" label="Bold (Ctrl+B)"><Bold /></Item>
        <Item value="italic" label="Italic (Ctrl+I)"><Italic /></Item>
        <Item value="underline" label="Underline (Ctrl+U)"><Underline /></Item>
        <Item value="strike" label="Strikethrough"><Strikethrough /></Item>
      </ToggleGroup>

      <select aria-label="Case" className={SELECT} value={style.case}
        onChange={(event) => set('case', event.target.value as LabelStyle['case'])}>
        {CASES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>

      <ToggleGroup type="single" variant="outline" size="sm" spacing={0} aria-label="Alignment" value={style.align}
        onValueChange={(align) => align && set('align', align as LabelStyle['align'])}>
        <Item value="left" label="Align left"><TextAlignStart /></Item>
        <Item value="center" label="Centre"><TextAlignCenter /></Item>
        <Item value="right" label="Align right"><TextAlignEnd /></Item>
      </ToggleGroup>

      <select aria-label="Line spacing" className={SELECT} value={style.line_spacing}
        onChange={(event) => set('line_spacing', Number(event.target.value) as LabelStyle['line_spacing'])}>
        {SPACINGS.map((spacing) => <option key={spacing} value={spacing}>{spacing.toFixed(1)} line spacing</option>)}
      </select>

      <ToggleGroup type="single" variant="outline" size="sm" spacing={0} aria-label="Text direction"
        value={style.direction}
        onValueChange={(direction) => direction && set('direction', direction as LabelStyle['direction'])}>
        <ToggleGroupItem value="horizontal" title="Text runs along the tape">Horizontal</ToggleGroupItem>
        <ToggleGroupItem value="vertical" title="Text turned 90 degrees, lines run across the tape">
          Vertical
        </ToggleGroupItem>
      </ToggleGroup>

      <ToggleGroup type="multiple" variant="outline" size="sm" spacing={0} aria-label="Effects"
        value={flagsFrom(style, EFFECTS)} onValueChange={(on) => onChange(withFlags(style, EFFECTS, on))}>
        <ToggleGroupItem value="outline">Outline</ToggleGroupItem>
        <ToggleGroupItem value="invert">White on black</ToggleGroupItem>
        <ToggleGroupItem value="border">Border</ToggleGroupItem>
      </ToggleGroup>

      <Button variant="ghost" size="sm" onClick={() => onChange(DEFAULT_STYLE)}
        disabled={JSON.stringify(style) === JSON.stringify(DEFAULT_STYLE)}>
        <RemoveFormatting aria-hidden />
        Clear formatting
      </Button>
    </div>
  )
}

function Item({ value, label, children }: { value: string; label: string; children: ReactNode }) {
  return (
    <ToggleGroupItem value={value} aria-label={label} title={label}>
      {children}
    </ToggleGroupItem>
  )
}

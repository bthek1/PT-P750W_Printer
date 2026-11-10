/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_STYLE, toggleFlag, type LabelStyle } from './api'
import { FormatToolbar } from './FormatToolbar'

afterEach(cleanup)

function setup(style: LabelStyle = DEFAULT_STYLE) {
  const onChange = vi.fn()
  render(<FormatToolbar style={style} onChange={onChange} />)
  return onChange
}

describe('FormatToolbar', () => {
  it.each([
    ['Font', 'mono', { font: 'mono' }],
    ['Font size', '18', { size_pt: 18 }],
    ['Case', 'title', { case: 'title' }],
    ['Line spacing', '2', { line_spacing: 2 }],
  ])('%s select sets %s', (label, value, change) => {
    const onChange = setup()
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_STYLE, ...change })
  })

  it('sets size back to auto as null', () => {
    const onChange = setup({ ...DEFAULT_STYLE, size_pt: 12 })
    expect(screen.getByLabelText('Font size')).toHaveValue('12')
    fireEvent.change(screen.getByLabelText('Font size'), { target: { value: '' } })
    expect(onChange).toHaveBeenCalledWith(DEFAULT_STYLE)
  })

  it.each([
    [/Bold/, { bold: false }],
    [/Italic/, { italic: true }],
    [/Underline/, { underline: true }],
    [/Strikethrough/, { strike: true }],
    ['Outline', { outline: true }],
    ['White on black', { invert: true }],
    ['Border', { border: true }],
  ])('toggle %s changes only its own flag', (name, change) => {
    const onChange = setup()
    fireEvent.click(screen.getByRole('button', { name }))
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_STYLE, ...change })
  })

  it('shows pressed state from the style', () => {
    setup({ ...DEFAULT_STYLE, bold: false, underline: true, border: true })
    expect(screen.getByRole('button', { name: /Bold/ })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: /Underline/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Border' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('sets alignment and never leaves it empty', () => {
    const onChange = setup()
    fireEvent.click(screen.getByRole('radio', { name: 'Align right' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_STYLE, align: 'right' })
    onChange.mockClear()
    // Clicking the active item would deselect it in a Radix single group.
    fireEvent.click(screen.getByRole('radio', { name: 'Centre' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('clear formatting restores the defaults and is disabled at the defaults', () => {
    const onChange = setup({ ...DEFAULT_STYLE, invert: true, font: 'serif' })
    fireEvent.click(screen.getByRole('button', { name: 'Clear formatting' }))
    expect(onChange).toHaveBeenCalledWith(DEFAULT_STYLE)
    cleanup()
    setup()
    expect(screen.getByRole('button', { name: 'Clear formatting' })).toBeDisabled()
  })

  it('gives dropdown options the theme colours', () => {
    // Native option popups ignore the page's dark background; see index.css.
    setup()
    expect(screen.getByLabelText('Font').className).toContain('[&_option]:bg-popover')
    expect(screen.getByLabelText('Font').className).toContain('[&_option]:text-popover-foreground')
  })
})

describe('toggleFlag', () => {
  it('flips one flag and keeps the rest', () => {
    expect(toggleFlag(DEFAULT_STYLE, 'outline')).toEqual({ ...DEFAULT_STYLE, outline: true })
    expect(toggleFlag(toggleFlag(DEFAULT_STYLE, 'bold'), 'bold')).toEqual(DEFAULT_STYLE)
  })
})

describe('index.css', () => {
  it('switches native controls to dark in dark mode', () => {
    // Without this, select popups render white with the page's white text.
    // Read from disk: Vitest replaces CSS imports with an empty string.
    const css = readFileSync('src/index.css', 'utf8')
    expect(css).toMatch(/\.dark\s*\{[^}]*color-scheme:\s*dark/)
  })
})

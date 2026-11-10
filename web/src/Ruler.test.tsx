import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Ruler } from './Ruler'

const PX_PER_MM = 180 / 25.4

afterEach(cleanup)

function labels(name: string) {
  return Array.from(screen.getByRole('img', { name }).querySelectorAll('text')).map((t) => t.textContent)
}

describe('Ruler', () => {
  it('numbers 24 mm tape every 5 mm with the end marked', () => {
    render(<Ruler orientation="vertical" mm={24} pxPerMm={PX_PER_MM} label="w" />)
    expect(labels('w')).toEqual(['0', '5', '10', '15', '20', '24 mm'])
    expect(screen.getByRole('img', { name: 'w' })).toHaveAttribute('height', '170')
  })

  it('drops a number that crowds the end', () => {
    render(<Ruler orientation="vertical" mm={12} pxPerMm={PX_PER_MM} label="w" />)
    expect(labels('w')).toEqual(['0', '5', '12 mm'])
  })

  it('ends a horizontal ruler at a fractional length', () => {
    render(<Ruler orientation="horizontal" mm={23.5} pxPerMm={PX_PER_MM} label="l" />)
    expect(labels('l')).toEqual(['0', '5', '10', '15', '23.5 mm'])
    // One tick per whole mm plus the end tick.
    expect(screen.getByRole('img', { name: 'l' }).querySelectorAll('line')).toHaveLength(25)
  })

  it('runs past a marker and drops the numbers under its text', () => {
    render(<Ruler orientation="horizontal" mm={40} pxPerMm={PX_PER_MM} marker={23.5} label="l" />)
    // 20 sits under "23.5 mm" and 25 is too close to the marker line; 40 is the open end.
    expect(labels('l')).toEqual(['0', '5', '10', '15', '30', '35', '40', '23.5 mm'])
    expect(screen.getByTestId('ruler-marker')).toHaveTextContent('23.5 mm')
    expect(screen.getByRole('img', { name: 'l' })).toHaveAttribute('width', '283')
  })
})

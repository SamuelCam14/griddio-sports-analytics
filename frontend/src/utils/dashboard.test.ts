import assert from 'node:assert'
import { describe, it } from 'node:test'
import {
  formatEvDollars,
  formatPercentage,
  formatNumber,
  formatMatchDate,
  formatMatchTime,
} from './dashboard.ts'

describe('formatEvDollars', () => {
  it('formats positive values with plus sign and dollar prefix', () => {
    assert.strictEqual(formatEvDollars(2.5, 1), '+$2.5')
    assert.strictEqual(formatEvDollars(10.75, 2), '+$10.75')
  })

  it('formats negative values with minus sign and dollar prefix without double signs', () => {
    assert.strictEqual(formatEvDollars(-1.5, 1), '-$1.5')
    assert.strictEqual(formatEvDollars(-4.25, 2), '-$4.25')
  })

  it('formats zero and negative zero consistently as positive currency', () => {
    assert.strictEqual(formatEvDollars(0, 1), '+$0.0')
    assert.strictEqual(formatEvDollars(-0, 1), '+$0.0')
    assert.strictEqual(formatEvDollars(-0.001, 1), '+$0.0')
  })

  it('handles negative numbers rounding to zero or non-zero properly', () => {
    assert.strictEqual(formatEvDollars(-0.04, 1), '+$0.0')
    assert.strictEqual(formatEvDollars(-0.06, 1), '-$0.1')
    assert.strictEqual(formatEvDollars(-0.004, 2), '+$0.00')
    assert.strictEqual(formatEvDollars(-0.006, 2), '-$0.01')
  })

  it('supports 0 digits and default digits', () => {
    assert.strictEqual(formatEvDollars(2.7, 0), '+$3')
    assert.strictEqual(formatEvDollars(-2.7, 0), '-$3')
    assert.strictEqual(formatEvDollars(4.5), '+$4.5')
  })

  it('safely clamps invalid digits arguments without throwing RangeError', () => {
    assert.strictEqual(formatEvDollars(2.5, -1), '+$3')
    assert.strictEqual(formatEvDollars(2.5, 999), '+$2.50000000000000000000')
    assert.strictEqual(formatEvDollars(2.5, NaN as any), '+$2.5')
  })

  it('applies suffix correctly', () => {
    assert.strictEqual(formatEvDollars(2.5, 1, { suffix: ' EV' }), '+$2.5 EV')
    assert.strictEqual(formatEvDollars(-1.5, 1, { suffix: ' EV' }), '-$1.5 EV')
    assert.strictEqual(formatEvDollars(3.0, 1, { suffix: ' / $100' }), '+$3.0 / $100')
    assert.strictEqual(formatEvDollars(2.5, 1, { suffix: '/100' }), '+$2.5/100')
  })

  it('safely handles non-finite numbers and missing inputs with fallback', () => {
    assert.strictEqual(formatEvDollars(null, 1), '—')
    assert.strictEqual(formatEvDollars(undefined, 1), '—')
    assert.strictEqual(formatEvDollars(NaN, 1), '—')
    assert.strictEqual(formatEvDollars(Infinity, 1), '—')
    assert.strictEqual(formatEvDollars(-Infinity, 1), '—')
  })

  it('supports custom fallback strings when provided', () => {
    assert.strictEqual(
      formatEvDollars(null, 1, { fallback: 'no calculado', suffix: ' por cada $100' }),
      'no calculado'
    )
    assert.strictEqual(
      formatEvDollars(NaN, 1, { fallback: 'no calculado' }),
      'no calculado'
    )
  })
})

describe('formatPercentage', () => {
  it('formats valid decimal probabilities into percentages', () => {
    assert.strictEqual(formatPercentage(0.554), '55.4%')
    assert.strictEqual(formatPercentage(1.0), '100.0%')
    assert.strictEqual(formatPercentage(0), '0.0%')
  })

  it('returns fallback for null, undefined, and non-finite values', () => {
    assert.strictEqual(formatPercentage(null), '—')
    assert.strictEqual(formatPercentage(undefined), '—')
    assert.strictEqual(formatPercentage(NaN), '—')
    assert.strictEqual(formatPercentage(Infinity), '—')
  })
})

describe('formatNumber', () => {
  it('formats valid numbers with specified digits', () => {
    assert.strictEqual(formatNumber(12.345, 1), '12.3')
    assert.strictEqual(formatNumber(12.345, 2), '12.35')
  })

  it('returns fallback for null, undefined, and non-finite values', () => {
    assert.strictEqual(formatNumber(null), '—')
    assert.strictEqual(formatNumber(undefined), '—')
    assert.strictEqual(formatNumber(NaN), '—')
  })
})

describe('formatMatchDate and formatMatchTime', () => {
  it('safely returns fallback for invalid or null date strings', () => {
    assert.strictEqual(formatMatchDate(null), '—')
    assert.strictEqual(formatMatchDate(undefined), '—')
    assert.strictEqual(formatMatchDate('invalid-date'), '—')
    assert.strictEqual(formatMatchTime(null), '—')
    assert.strictEqual(formatMatchTime(undefined), '—')
    assert.strictEqual(formatMatchTime('invalid-time'), '—')
  })

  it('formats valid ISO dates', () => {
    const formatted = formatMatchDate('2026-10-26T18:00:00Z')
    assert.ok(formatted !== '—')
    const timeFormatted = formatMatchTime('2026-10-26T18:00:00Z')
    assert.ok(timeFormatted !== '—')
  })
})

describe('NFL Totals UI presentation helpers', () => {
  it('formats over/under probabilities accurately with formatPercentage', () => {
    assert.strictEqual(formatPercentage(0.5432), '54.3%')
    assert.strictEqual(formatPercentage(0.5), '50.0%')
    assert.strictEqual(formatPercentage(0.4568), '45.7%')
  })

  it('formats projected total points with 1 decimal digit and fallback', () => {
    assert.strictEqual(formatNumber(48.5, 1), '48.5')
    assert.strictEqual(formatNumber(45.0, 1), '45.0')
    assert.strictEqual(formatNumber(null, 1), '—')
    assert.strictEqual(formatNumber(NaN, 1), '—')
    assert.strictEqual(formatNumber(Infinity, 1), '—')
  })

  it('formats push label and probability correctly', () => {
    const pick = 'PUSH'
    const prob = 0.5
    const label = pick === 'PUSH' ? 'PUSH (50%)' : `${pick} (${formatPercentage(prob)})`
    assert.strictEqual(label, 'PUSH (50%)')
  })

  it('formats over/under pick labels correctly when probability is available or missing', () => {
    const pickOver = 'OVER'
    const probOver = 0.582
    const labelOver = `${pickOver} (${formatPercentage(probOver)})`
    assert.strictEqual(labelOver, 'OVER (58.2%)')

    const labelMissing = `${pickOver} (${formatPercentage(null)})`
    assert.strictEqual(labelMissing, 'OVER (—)')
  })

  it('determines neutral totals state when pick is PUSH or odds are absent', () => {
    const isNeutralPush = (pick: string | null, odds: number | null) =>
      pick === 'PUSH' || !pick || odds == null

    assert.strictEqual(isNeutralPush('PUSH', -110), true)
    assert.strictEqual(isNeutralPush('OVER', null), true)
    assert.strictEqual(isNeutralPush('UNDER', null), true)
    assert.strictEqual(isNeutralPush('OVER', -110), false)
    assert.strictEqual(isNeutralPush(null, -110), true)
  })
})

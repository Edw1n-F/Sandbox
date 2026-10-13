/**
 * The provenance receipt is built from the run alone.
 *
 * The properties worth pinning: it is null when there is no run (a "general"
 * answer must not display a receipt for data it did not use), it never names a
 * dataset the run did not report, and it still works on a run from before
 * `sources` existed.
 */
import { describe, expect, it } from 'vitest'

import { buildReceipt } from './receipt'
import type { RunResult } from './types'

function run(over: Partial<RunResult> = {}): RunResult {
  return {
    runId: 'abcdef1234567890',
    sceneId: 's1',
    inputs: { rainfallMm: 299.6, temperatureC: 30, planId: null, planIds: [] },
    overlays: { wetGridUrl: '/w.png', heatmapUrl: '/h.png' },
    stats: {} as RunResult['stats'],
    worsens: false,
    interventionsApplied: [],
    interventionsSkipped: [],
    explanation: '',
    explanationSource: 'template',
    status: 'SCREENING_ESTIMATE',
    warning: '',
    ...over,
  } as RunResult
}

const value = (r: ReturnType<typeof buildReceipt>, label: string) =>
  r?.lines.find((l) => l.label === label)?.value

describe('buildReceipt', () => {
  it('is null without a run, so a general answer shows no receipt', () => {
    expect(buildReceipt(null)).toBeNull()
    expect(buildReceipt(undefined)).toBeNull()
  })

  it('names the run and rounds rainfall to a whole millimetre', () => {
    const r = buildReceipt(run())
    expect(value(r, 'Run')).toBe('abcdef1234567890')
    expect(value(r, 'Rainfall')).toBe('300 mm total')
    expect(r?.summary).toBe('Run abcdef12 · 300 mm')
  })

  it('says no projects were applied rather than implying a plan', () => {
    expect(value(buildReceipt(run()), 'Scenario')).toMatch(/no projects/)
  })

  it('counts projects, with the right plural', () => {
    const one = run({ inputs: { rainfallMm: 100, temperatureC: 30, planId: 'a', planIds: ['a'] } })
    const two = run({ inputs: { rainfallMm: 100, temperatureC: 30, planId: 'a', planIds: ['a', 'b'] } })
    expect(value(buildReceipt(one), 'Scenario')).toBe('1 project applied')
    expect(value(buildReceipt(two), 'Scenario')).toBe('2 projects applied')
  })

  it('reports the data sources the run named, and only those', () => {
    const r = buildReceipt(
      run({ sources: { terrain: 'open3dhk-tiles', landCover: 'esa-worldcover', lst: 'Landsat 8/9' } }),
    )
    expect(value(r, 'Terrain')).toBe('open3dhk-tiles')
    expect(value(r, 'Land cover')).toBe('esa-worldcover')
    expect(value(r, 'Heat anchor')).toBe('Landsat 8/9')
  })

  it('omits the heat anchor when the run had none, instead of inventing one', () => {
    const r = buildReceipt(
      run({ sources: { terrain: 'open3dhk-tiles', landCover: 'esa-worldcover', lst: null } }),
    )
    expect(value(r, 'Heat anchor')).toBeUndefined()
  })

  it('still works on an older run that predates `sources`', () => {
    const r = buildReceipt(run({ sources: undefined }))
    expect(r).not.toBeNull()
    expect(value(r, 'Terrain')).toBeUndefined()
    expect(value(r, 'Land cover')).toBeUndefined()
  })

  it('includes tide and surface temperature only when the run reported them', () => {
    expect(value(buildReceipt(run()), 'Tide')).toBeUndefined()
    const r = buildReceipt(
      run({
        stats: {
          tide: { state: 'mean', label: 'Mean tide', tailwaterM: 1.4, source: 'HKO', active: true },
          temperature: {
            quantity: 'LST_skin', unit: 'C', observedMeanC: 41.3, scenarioMeanC: 42,
            surfaceToAirOffsetC: 12, source: 'x', note: '',
          },
        } as RunResult['stats'],
      }),
    )
    expect(value(r, 'Tide')).toBe('Mean tide')
    expect(value(r, 'Surface temperature')).toBe('42.0 °C scenario mean')
  })

  it('ends with the run status, so the screening label travels with the numbers', () => {
    const r = buildReceipt(run())
    expect(r?.lines[r.lines.length - 1]).toEqual({ label: 'Status', value: 'SCREENING_ESTIMATE' })
  })
})

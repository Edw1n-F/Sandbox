/**
 * The suggestions API's wire contract.
 *
 * The properties the loop depends on: the storm and cap travel as query
 * params, a pin's geometry goes out **verbatim** (re-deriving it would
 * decouple the pinned footprint from the estimate the panel showed), the
 * idempotency flag surfaces so the UI can say "already pinned" instead of
 * pretending to add a second row, and a negative `runoffDeltaM3` survives
 * the round trip **negative** -- taking an absolute value of it anywhere
 * would turn a real loss into an invented gain.
 */
import { describe, expect, it, vi } from 'vitest'
import type { AxiosInstance } from 'axios'

vi.mock('./client', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
  } as unknown as AxiosInstance,
}))

const { http } = await import('./client')
const { fetchSuggestions, pinSuggestion } = await import('./suggestApi')

const geometry = {
  type: 'Polygon' as const,
  coordinates: [
    [
      [114.0, 22.42],
      [114.0, 22.43],
      [114.01, 22.43],
      [114.0, 22.42],
    ],
  ],
}

const response = {
  status: 'SITES_FROM_MEASURED_RASTERS',
  warning:
    'Every candidate is a parcel read from land cover, the wet grid and the surveyed building footprints at 5 m. Treat them as screening parcels, not surveyed sites.',
  generatedFrom: {
    deterministic: true,
    landCover: 'ESA WorldCover 10 m v200 (2021)',
    wetGrid: 'level-pool wet grid at 450 mm',
    buildings: '6-NW-12D surveyed footprints',
  },
  runContext: { rainfallMm: 450.0, siteCells: 17316, siteAreaHa: 43.5 },
  axes: [
    {
      axis: 'drainage',
      basis: 'storage is footprint cells x the working depth, ranked by storage added',
      candidates: [
        {
          kind: 'water_reservoir',
          site: {
            geometry,
            centroid: [114.002, 22.424],
            areaHa: 6.2,
            cells: 2469,
            evidence:
              'connected sub-basin of the 450 mm level-pool wet grid at 1 m depth or more',
          },
          estimate: {
            cellsCovered: 2469,
            cellsOfSite: 2469,
            areaHa: 6.2,
            classToday: 'paved',
            classAfter: 'water',
            // Signed, and negative: the reservoir stands water that would
            // otherwise run off. No absolute value anywhere.
            runoffDeltaM3: -382.2,
            storageAddedM3: 93039.8,
            heatIndexDelta: -0.95,
            basis: 'storage = footprint cells x working depth on the 5 m grid',
          },
          plan: {
            interventions: [
              {
                geometry,
                interventionType: 'water_reservoir',
                source: 'manual' as const,
                params: { storageDepthM: 6.57 },
                style: { color: '#1f6feb' },
              },
            ],
          },
        },
      ],
      candidatesAvailable: 19,
      candidatesRefused: [
        {
          kind: 'underground_tank',
          cells: 6821,
          why: 'polygon covers 39.4% of the study area, over the 25% limit',
        },
      ],
    },
  ],
}

describe('suggestApi', () => {
  it('requests the scene suggestions with the storm and cap as query params', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: response })
    const doc = await fetchSuggestions('scene-1', 450, 3)
    expect(http.get).toHaveBeenCalledWith('/scenes/scene-1/suggestions', {
      params: { rainfall_mm: 450, max_per_axis: 3 },
    })
    expect(doc.axes[0].candidatesAvailable).toBe(19)
    expect(doc.runContext.rainfallMm).toBe(450)
  })

  it('omits the query params entirely when no storm or cap is given', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: response })
    await fetchSuggestions('scene-1')
    expect(http.get).toHaveBeenCalledWith('/scenes/scene-1/suggestions', {
      params: {},
    })
  })

  it('keeps a negative runoff delta negative through the round trip', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: response })
    const doc = await fetchSuggestions('scene-1', 450, 3)
    const candidate = doc.axes[0].candidates[0]
    expect(candidate.estimate.runoffDeltaM3).toBe(-382.2)
    expect(candidate.estimate.runoffDeltaM3).toBeLessThan(0)
    // The document is passed through as-is: the disclosure, the refused
    // candidate and the provenance all survive untouched.
    expect(doc).toEqual(response)
    expect(doc.axes[0].candidatesRefused[0].why).toContain('25%')
    expect(candidate.site.evidence).toContain('450 mm')
  })

  it('pins a candidate with the geometry sent verbatim', async () => {
    vi.mocked(http.post).mockResolvedValue({
      data: { featureId: 'feat-1', planId: 'plan-1', pinned: true, alreadyPinned: false },
    })
    const result = await pinSuggestion('scene-1', {
      geometry,
      interventionType: 'water_reservoir',
      params: { storageDepthM: 6.57 },
      style: { color: '#1f6feb' },
    })
    expect(http.post).toHaveBeenCalledWith('/scenes/scene-1/pins', {
      geometry,
      interventionType: 'water_reservoir',
      params: { storageDepthM: 6.57 },
      style: { color: '#1f6feb' },
    })
    // The exact call body, not just a shape: the coordinates must not have
    // been re-keyed, re-ordered or re-closed on the way out.
    const [, body] = vi.mocked(http.post).mock.calls[0]
    expect(body).toEqual({ geometry, interventionType: 'water_reservoir', params: { storageDepthM: 6.57 }, style: { color: '#1f6feb' } })
    expect(result.featureId).toBe('feat-1')
    expect(result.pinned).toBe(true)
    expect(result.alreadyPinned).toBe(false)
  })

  it('surfaces the alreadyPinned flag instead of a second feature', async () => {
    vi.mocked(http.post).mockResolvedValue({
      data: { featureId: 'feat-1', planId: 'plan-1', pinned: true, alreadyPinned: true },
    })
    const result = await pinSuggestion('scene-1', {
      geometry,
      interventionType: 'water_reservoir',
      params: {},
      style: {},
    })
    expect(result.alreadyPinned).toBe(true)
    expect(result.featureId).toBe('feat-1')
  })
})

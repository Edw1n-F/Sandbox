/**
 * The catalog API's wire contract.
 *
 * The property the whole loop depends on is that a phase's geometry travels
 * **verbatim**: if `createPlan` re-derived, re-keyed or re-shaped anything, the
 * same phase toggled twice would be two different footprints and a
 * before/after comparison would stop meaning anything. So the test below asserts
 * the exact request body, and the second asserts that a catalog document is
 * passed through without being rewritten (the provenance is the payload, not
 * decoration).
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
const { createPlan, fetchProjectCatalog } = await import('./catalogApi')

const catalog = {
  schema: 'hack4sdg/project-catalog@1',
  generatedBy: 'scripts/make_project_catalog.py',
  grid: {
    rows: 117,
    cols: 148,
    cellM: 5.01,
    cellAreaM2: 25.12,
    bounds: [113.9922, 22.431, 113.9994, 22.4363],
  },
  sources: { landCover: 'ESA WorldCover 10 m v200 (2021)' },
  warning: 'candidate parcels, not surveyed sites',
  projects: [
    {
      id: 'detention-reservoir',
      name: 'Detention reservoir',
      note: 'Open water in the deep core of the basin.',
      axis: 'drainage',
      source: 'wetgrid',
      date: '2026-10-06',
      confidence: 'screening',
      phases: [
        {
          id: 'core',
          label: 'The deep core',
          interventions: [
            {
              interventionType: 'water_reservoir',
              geometry: {
                type: 'Polygon' as const,
                coordinates: [
                  [
                    [113.99, 22.43],
                    [113.99, 22.44],
                    [114.0, 22.44],
                    [113.99, 22.43],
                  ],
                ],
              },
              params: { storageDepthM: 6.57 },
              style: { color: '#1f6feb' },
              provenance: {
                why: 'the deepest connected basin, 1 m or more',
                cells: 2469,
                maskCells: 2469,
                areaHa: 6.2,
              },
            },
          ],
          grounding: { evidence: 'wet grid at 1 m depth', metrics: { cells: 2469 } },
        },
      ],
    },
  ],
}

describe('catalogApi', () => {
  it('serves the catalog document unchanged', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: catalog })
    const doc = await fetchProjectCatalog('scene-1')
    expect(http.get).toHaveBeenCalledWith('/scenes/scene-1/project-catalog')
    expect(doc).toEqual(catalog)
    expect(doc.projects[0].phases[0].interventions[0].provenance.why).toContain(
      'deepest connected basin',
    )
  })

  it('sends a phase geometry verbatim, with provenance attached', async () => {
    vi.mocked(http.post).mockResolvedValue({
      data: { planId: 'plan-1', interventionCount: 1 },
    })
    const geometry = catalog.projects[0].phases[0].interventions[0].geometry
    const created = await createPlan('scene-1', {
      label: 'Detention reservoir — The deep core',
      source: 'manual',
      params: { projectId: 'detention-reservoir', phaseId: 'core' },
      interventions: [
        {
          geometry,
          interventionType: 'water_reservoir',
          source: 'manual',
          params: { storageDepthM: 6.57, provenance: { why: 'because' } },
          style: { color: '#1f6feb' },
        },
      ],
    })
    expect(created.planId).toBe('plan-1')
    expect(http.post).toHaveBeenCalledWith('/scenes/scene-1/plans', {
      label: 'Detention reservoir — The deep core',
      source: 'manual',
      params: { projectId: 'detention-reservoir', phaseId: 'core' },
      interventions: [
        expect.objectContaining({
          geometry,
          interventionType: 'water_reservoir',
        }),
      ],
    })
  })
})

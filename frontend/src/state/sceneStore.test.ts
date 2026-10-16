/**
 * Unit tests for the scene store's reducers.
 *
 * Two of these are regression tests for a documented trap rather than coverage for its
 * own sake. `setEnvParams` writes to the live scene object, which ADR-024 warns is
 * local-only and must not be persisted; and the layer toggles are session state that
 * several panels read, so a reducer that mutated in place would make every subscriber
 * miss the change.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSceneStore } from './sceneStore'
import type { RunResult, Scene } from '@/api/types'

// Hoisted: `vi.mock` factories run before the module body, so the spy has to be
// created where the factory can see it.
const { runSimulation } = vi.hoisted(() => ({ runSimulation: vi.fn() }))
vi.mock('@/api/sceneApi', () => ({
  getScene: vi.fn(),
  getTerrain: vi.fn(),
  runSimulation,
}))

const scene = {
  sceneId: 's1',
  name: 'Test',
  center: { coordinates: [113.99, 22.44] as [number, number] },
  bbox: [[113.98, 22.43], [114.0, 22.45]] as [[number, number], [number, number]],
  envParams: { rainfall: 450, temperature: 30 },
  defaultCamera: { longitude: 113.99, latitude: 22.44, height: 4200, heading: 0, pitch: -45 },
} as unknown as Scene

const reset = () => {
  useSceneStore.setState({
    sceneId: '',
    scene: null,
    terrain: null,
    loading: false,
    error: null,
    layers: {
      boundary: true,
      wetGrid: false,
      heatmap: false,
      interventions: false,
      props: true,
      realBuildings: true,
      massing25d: false,
    },
    viewMode: 'survey',
  })
}

describe('layer toggles', () => {
  beforeEach(reset)

  it('flips one layer without touching the others', () => {
    const before = useSceneStore.getState().layers
    useSceneStore.getState().toggleLayer('wetGrid')
    const after = useSceneStore.getState().layers
    expect(after.wetGrid).toBe(!before.wetGrid)
    expect(after.boundary).toBe(before.boundary)
  })

  it('replaces the object so subscribers see the change', () => {
    // Zustand compares by reference. Mutating `layers` in place would leave every
    // `useSceneStore((s) => s.layers)` selector returning the same object, so the
    // layer switches would stop responding with no error anywhere.
    const before = useSceneStore.getState().layers
    useSceneStore.getState().toggleLayer('boundary')
    expect(useSceneStore.getState().layers).not.toBe(before)
  })

  it('merges a partial set without clearing the rest', () => {
    useSceneStore.setState((s) => ({ layers: { ...s.layers, heatmap: true } }))
    useSceneStore.getState().setLayers({ interventions: true })
    const layers = useSceneStore.getState().layers
    expect(layers.heatmap).toBe(true)
    expect(layers.interventions).toBe(true)
    expect(layers.boundary).toBe(true)
  })
})

describe('setEnvParams', () => {
  beforeEach(reset)

  it('writes a slider value onto the scene', () => {
    useSceneStore.setState({ scene })
    useSceneStore.getState().setEnvParams({ rainfall: 600 })
    expect(useSceneStore.getState().scene?.envParams.rainfall).toBe(600)
  })

  it('replaces the scene and envParams objects rather than mutating them', () => {
    // The whole reason ADR-024 exists: every plan hangs off the scene object, so an
    // in-place write would mutate shared state that other panels are holding.
    useSceneStore.setState({ scene })
    const sceneBefore = useSceneStore.getState().scene
    const paramsBefore = sceneBefore!.envParams
    useSceneStore.getState().setEnvParams({ temperature: 31 })
    expect(useSceneStore.getState().scene).not.toBe(sceneBefore)
    expect(useSceneStore.getState().scene!.envParams).not.toBe(paramsBefore)
    expect(sceneBefore!.envParams.temperature).toBe(30)
  })

  it('is a no-op with no scene loaded', () => {
    const before = useSceneStore.getState()
    useSceneStore.getState().setEnvParams({ rainfall: 999 })
    expect(useSceneStore.getState().scene).toBe(before.scene)
  })
})

/**
 * The run loop's wiring, which is the difference between a panel that describes
 * a simulation and one that runs it. These cover what the browser suites cannot
 * reach from here: that the slider values actually travel in the request, and
 * that a result is pointed at the viewer rather than only stored.
 */
describe('run', () => {
  beforeEach(() => {
    reset()
    runSimulation.mockReset()
    useSceneStore.setState({
      runResult: null,
      running: false,
      runError: null,
    })
  })

  const result = {
    runId: 'abc12345',
    sceneId: 's1',
    inputs: { rainfallMm: 600, temperatureC: 38,
      planId: 'plan-1' },
    overlays: { wetGridUrl: '/static/rasters/run/wetgrid.png?v=abc12345',
      heatmapUrl: '/static/rasters/run/heatmap.png?v=abc12345' },
    stats: { baseline: {}, scenario: {}, delta: {} },
    worsens: false,
    interventionsApplied: [],
    interventionsSkipped: [],
    explanation: 'text',
    explanationSource: 'template',
    status: 's',
    warning: 'w',
  } as unknown as RunResult

  it('sends the slider values and the selected plans, not the scene defaults', async () => {
    useSceneStore.setState({ sceneId: 's1', scene })
    useSceneStore.getState().setEnvParams({ precipitation: 30, durationHours: 4, temperature: 38 })
    runSimulation.mockResolvedValue(result)
    await useSceneStore.getState().run(['plan-1'])
    expect(runSimulation).toHaveBeenCalledWith('s1', {
      // intensity 30 mm/hr × 4 h = 120 mm storm-total, the value SCS-CN needs.
      rainfallMm: 120,
      temperatureC: 38,
      tideState: 'low',
      planId: 'plan-1',
      planIds: ['plan-1'],
    })
  })

  it('sends every selected plan, so the run is cumulative', async () => {
    // The brief's goal is the combined effect, so the whole selection travels --
    // not just the plan the viewer happens to highlight.
    useSceneStore.setState({ sceneId: 's1', scene })
    runSimulation.mockResolvedValue(result)
    await useSceneStore.getState().run(['plan-1', 'plan-2', 'plan-3'])
    expect(runSimulation).toHaveBeenCalledWith('s1', {
      rainfallMm: 50,
      temperatureC: 30,
      tideState: 'low',
      planId: 'plan-1',
      planIds: ['plan-1', 'plan-2', 'plan-3'],
    })
  })

  it('falls back to the documented defaults when the scene carries none', async () => {
    useSceneStore.setState({ sceneId: 's1', scene: { ...scene, envParams: {} } })
    runSimulation.mockResolvedValue(result)
    await useSceneStore.getState().run(null)
    expect(runSimulation).toHaveBeenCalledWith('s1', {
      rainfallMm: 50,
      temperatureC: 30,
      tideState: 'low',
      planId: null,
      planIds: [],
    })
  })

  it('points the viewer at the new overlays and switches them on', async () => {
    // A run whose result the planner cannot see is indistinguishable from a run
    // that failed, so both overlays come on with the result.
    useSceneStore.setState({
      sceneId: 's1',
      scene,
      terrain: { terrainUrl: null, imageryUrl: null, bbox: null,
        wetGridUrl: '/old', heatmapUrl: '/old' },
    })
    runSimulation.mockResolvedValue(result)
    await useSceneStore.getState().run(null)
    const state = useSceneStore.getState()
    expect(state.terrain?.wetGridUrl).toBe(result.overlays.wetGridUrl)
    expect(state.terrain?.heatmapUrl).toBe(result.overlays.heatmapUrl)
    expect(state.layers.wetGrid).toBe(true)
    expect(state.layers.heatmap).toBe(true)
    expect(state.runResult).toBe(result)
    expect(state.running).toBe(false)
  })

  it('records a failure instead of leaving the run hanging', async () => {
    useSceneStore.setState({ sceneId: 's1', scene })
    runSimulation.mockRejectedValue(new Error('backend unreachable'))
    await useSceneStore.getState().run(null)
    const state = useSceneStore.getState()
    expect(state.running).toBe(false)
    expect(state.runResult).toBeNull()
    expect(state.runError).toMatch(/unreachable/)
  })

  it('does nothing without a scene', async () => {
    await useSceneStore.getState().run(null)
    expect(runSimulation).not.toHaveBeenCalled()
    expect(useSceneStore.getState().running).toBe(false)
  })
})

describe('view mode', () => {
  beforeEach(reset)

  it('starts in survey and switches', () => {
    expect(useSceneStore.getState().viewMode).toBe('survey')
    useSceneStore.getState().setViewMode('tactical')
    expect(useSceneStore.getState().viewMode).toBe('tactical')
  })
})

/**
 * Unit tests for the scene store's reducers.
 *
 * Two of these are regression tests for a documented trap rather than coverage for its
 * own sake. `setEnvParams` writes to the live scene object, which ADR-024 warns is
 * local-only and must not be persisted; and the layer toggles are session state that
 * several panels read, so a reducer that mutated in place would make every subscriber
 * miss the change.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { useSceneStore } from './sceneStore'
import type { Scene } from '@/api/types'

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
      imagery: true,
      boundary: true,
      wetGrid: false,
      heatmap: false,
      interventions: false,
      cityModel: true,
      props: true,
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
    expect(after.imagery).toBe(before.imagery)
    expect(after.cityModel).toBe(before.cityModel)
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
    expect(layers.imagery).toBe(true)
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

describe('view mode', () => {
  beforeEach(reset)

  it('starts in survey and switches', () => {
    expect(useSceneStore.getState().viewMode).toBe('survey')
    useSceneStore.getState().setViewMode('tactical')
    expect(useSceneStore.getState().viewMode).toBe('tactical')
  })
})

/**
 * Scene state — the frontend's source of truth (CLAUDE.md: "State | Zustand
 * stores — source of truth").
 *
 * The render layer reads from here and never fetches (CLAUDE.md: "The rendering
 * layer must not fetch data itself"). Data flows API -> State -> View.
 */
import { create } from 'zustand'

import { getScene, getTerrain } from '@/api/sceneApi'
import { errorMessage } from '@/api/client'
import type { EnvParams, LayerVisibility, Scene, TerrainMeta } from '@/api/types'

export interface SceneState {
  sceneId: string
  scene: Scene | null
  terrain: TerrainMeta | null
  loading: boolean
  error: string | null
  layers: LayerVisibility

  load: (sceneId: string) => Promise<void>
  setEnvParams: (patch: Partial<EnvParams>) => void
  toggleLayer: (layer: keyof LayerVisibility) => void
  setLayers: (layers: Partial<LayerVisibility>) => void
  clearError: () => void
}

export const DEFAULT_LAYERS: LayerVisibility = {
  imagery: true,
  boundary: true,
  wetGrid: false,
  heatmap: false,
  interventions: true,
  realBuildings: true,
  props: true,
  waterbody: true,
  infrastructure: true,
}

export const useSceneStore = create<SceneState>((set) => ({
  sceneId: '',
  scene: null,
  terrain: null,
  loading: false,
  error: null,
  layers: { ...DEFAULT_LAYERS },

  load: async (sceneId) => {
    set({ loading: true, error: null, sceneId })
    try {
      // The scene is the anchor: terrain is fetched alongside it because the
      // viewer cannot initialise without both, and a half-loaded viewer is
      // worse than a slightly slower one.
      const [scene, terrain] = await Promise.all([getScene(sceneId), getTerrain(sceneId)])
      set({ scene, terrain, loading: false })
    } catch (error) {
      set({ error: errorMessage(error), loading: false })
    }
  },

  /**
   * Slider handler.
   *
   * ADR-024: scene.envParams holds *static, location-derived defaults*. The
   * prototype writes slider values here because the physics engine that would
   * own per-run inputs does not exist yet — so the write is local-only and is
   * NOT persisted back to the API. Persisting a slider drag would mutate the
   * object every plan hangs off, which is exactly the trap ADR-024 warns about.
   */
  setEnvParams: (patch) =>
    set((state) => {
      if (!state.scene) return state
      return {
        scene: {
          ...state.scene,
          envParams: { ...state.scene.envParams, ...patch },
        },
      }
    }),

  toggleLayer: (layer) =>
    set((state) => ({ layers: { ...state.layers, [layer]: !state.layers[layer] } })),

  setLayers: (layers) => set((state) => ({ layers: { ...state.layers, ...layers } })),

  clearError: () => set({ error: null }),
}))

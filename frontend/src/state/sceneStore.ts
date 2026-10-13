/**
 * Scene state — the frontend's source of truth (CLAUDE.md: "State | Zustand
 * stores — source of truth").
 *
 * The render layer reads from here and never fetches (CLAUDE.md: "The rendering
 * layer must not fetch data itself"). Data flows API -> State -> View.
 */
import { create } from 'zustand'

import { getScene, getTerrain, runSimulation } from '@/api/sceneApi'
import { errorMessage } from '@/api/client'
import type {
  EnvParams,
  LayerVisibility,
  RunResult,
  Scene,
  TerrainMeta,
  ViewMode,
} from '@/api/types'

/**
 * The minimum a payload must carry to be treated as a scene.
 *
 * Deliberately shallow. The point is not to re-validate the whole contract -- that
 * is the schema layer's job -- it is to stop an unreadable object reaching the panels,
 * because a throw inside a React render unmounts the tree and the user gets a white
 * page instead of a sentence.
 */
function isScene(value: unknown): value is Scene {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<Scene>
  const centre = candidate.center?.coordinates
  return (
    Array.isArray(centre) &&
    centre.length >= 2 &&
    centre.every((n) => typeof n === 'number') &&
    !!candidate.bbox &&
    !!candidate.defaultCamera
  )
}

/**
 * Slider defaults, used when the scene carries no `envParams` of its own.
 *
 * 450 mm is the project's rainfall assumption and the number every committed
 * overlay was generated at, so a first run reproduces the artefact the user
 * can see before they move anything.
 */
export const DEFAULT_RAINFALL_MM = 450
export const DEFAULT_TEMPERATURE_C = 30

export interface SceneState {
  sceneId: string
  scene: Scene | null
  terrain: TerrainMeta | null
  loading: boolean
  error: string | null
  layers: LayerVisibility
  /**
   * The latest live run, and the state of the run in flight.
   *
   * Kept in the store rather than in the panel that triggered it: the viewer
   * reads the overlay URLs from here, so the result survives a panel remount
   * and stays visible while the user orbits the sandbox.
   */
  runResult: RunResult | null
  running: boolean
  runError: string | null
  /**
   * How the scene is dressed. Session-local like `layers`, and deliberately not
   * persisted: it is a viewing preference, not a property of the study area, so
   * putting it on the scene record would make a renderer choice look like data.
   */
  viewMode: ViewMode
  load: (sceneId: string) => Promise<void>
  setEnvParams: (patch: Partial<EnvParams>) => void
  run: (planIds: string[] | null) => Promise<void>
  toggleLayer: (layer: keyof LayerVisibility) => void
  setLayers: (layers: Partial<LayerVisibility>) => void
  setViewMode: (mode: ViewMode) => void
  clearError: () => void
}

export const DEFAULT_LAYERS: LayerVisibility = {
  imagery: true,
  boundary: true,
  // Overlays start off: they are extra context, and the study area is legible
  // without them. Interventions start on, because a scene whose plan has
  // features but shows none of them reads as a broken demo.
  wetGrid: false,
  heatmap: false,
  interventions: true,
  props: true,
  realBuildings: true,
  waterbody: true,
  infrastructure: true,
}

export const useSceneStore = create<SceneState>((set, get) => ({
  sceneId: '',
  scene: null,
  terrain: null,
  loading: false,
  error: null,
  layers: { ...DEFAULT_LAYERS },
  viewMode: 'tactical',
  runResult: null,
  running: false,
  runError: null,

  load: async (sceneId) => {
    set({ loading: true, error: null, sceneId })
    try {
      // The scene is the anchor: terrain is fetched alongside it because the
      // viewer cannot initialise without both, and a half-loaded viewer is
      // worse than a slightly slower one.
      const [scene, terrain] = await Promise.all([getScene(sceneId), getTerrain(sceneId)])
      // Refuse a payload that is not a scene, rather than storing it.
      //
      // Measured: a 200 whose body lacked the expected fields left the app with an
      // empty `#root` — a completely blank page, no message, no console error worth
      // reading. The panels then read `scene.center.coordinates` and the throw inside
      // a render unmounts the whole tree. A proxy serving the wrong body, or a
      // contract drift, would present to a user exactly like the app not loading.
      if (!isScene(scene)) {
        set({
          error:
            'The server returned a scene record this app cannot read (missing centre, ' +
            'bbox or default camera). Check that the API and frontend versions match.',
          loading: false,
        })
        return
      }
      set({ scene, terrain, loading: false })
    } catch (error) {
      set({ error: errorMessage(error), loading: false })
    }
  },

  /**
   * Slider handler.
   *
   * ADR-024: scene.envParams holds *static, location-derived defaults*, and the
   * write stays local-only — it is NOT persisted back to the API. Persisting a
   * slider drag would mutate the object every plan hangs off, which is exactly
   * the trap ADR-024 warns about.
   *
   * The values are read off here by :func:`run`, which sends them as the
   * per-run input they always were rather than as a property of the scene.
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

  /**
   * Run the simulation with the current slider values and the selected plans.
   *
   * `planIds: null` (or empty) is a baseline run. Every selected plan's
   * interventions are merged server-side into one scenario, which is what makes
   * cumulative comparison possible -- the brief's whole goal. The response's
   * overlay URLs are pushed onto `terrain` because that is what the viewer
   * reads, and both overlays are switched on: a run whose result the planner
   * cannot see is indistinguishable from a run that failed.
   */
  run: async (planIds) => {
    const { sceneId, scene } = get()
    if (!sceneId) return
    const selected = (planIds ?? []).filter(Boolean)
    set({ running: true, runError: null })
    try {
      const result = await runSimulation(sceneId, {
        rainfallMm: Number(
          scene?.envParams.precipitation ?? DEFAULT_RAINFALL_MM,
        ),
        temperatureC: Number(
          scene?.envParams.temperature ?? DEFAULT_TEMPERATURE_C,
        ),
        planId: selected[0] ?? null,
        planIds: selected,
      })
      set((state) => ({
        runResult: result,
        running: false,
        terrain: state.terrain
          ? {
              ...state.terrain,
              wetGridUrl: result.overlays.wetGridUrl,
              heatmapUrl: result.overlays.heatmapUrl,
            }
          : state.terrain,
        layers: { ...state.layers, wetGrid: true, heatmap: true },
      }))
    } catch (error) {
      set({ running: false, runError: errorMessage(error) })
    }
  },

  toggleLayer: (layer) =>
    set((state) => ({ layers: { ...state.layers, [layer]: !state.layers[layer] } })),

  setLayers: (layers) => set((state) => ({ layers: { ...state.layers, ...layers } })),

  /**
   * Switching mode must not move the camera. The tactical mode dims the basemap,
   * which reads as a change in what is being looked at, and a camera that jumped
   * at the same moment would make the mode look like a reload.
   */
  setViewMode: (mode) => set({ viewMode: mode }),


  clearError: () => set({ error: null }),
}))

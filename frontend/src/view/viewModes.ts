/**
 * View modes: the same data, rendered for a different job.
 *
 * `survey` is the daylight planning view — OpenStreetMap imagery, lit envelopes,
 * a map you read. `tactical` is the instrument view, inspired by OSIRIS
 * (simplifaisoul/osiris): near-black ground, a glowing reference grid, envelopes
 * drawn as dark volumes traced in neon, and a bloom pass so the neon glows.
 *
 * ## What it deliberately does not change
 *
 * **No data, and no claim.** Tactical mode re-styles the same real 3D Tiles
 * buildings and basemap. A prettier renderer is not a better dataset, and this
 * project has already been burned by that confusion.
 *
 * ## Why the scene, and not just the buildings
 *
 * Neon edges on a photographic basemap look like a mistake. The dark globe, the
 * dimmed imagery and the grid are what make the emissive envelopes read as a
 * deliberate instrument panel rather than as a rendering bug. So the mode is
 * applied at scene level and the buildings follow it.
 *
 * ## Reversibility
 *
 * Every value this module changes is captured before it is changed and put back
 * by the disposer. React effects run their cleanup under StrictMode's
 * double-mount, and a mode switch is just an effect re-run, so "restore exactly
 * what was found" is the only contract that survives both.
 */
import {
  Color,
  Rectangle,
  type ImageryLayer,
  type Viewer,
} from 'cesium'

import type { ViewMode } from '@/api/types'

export type { ViewMode }

const ACCENT = '#3fe0ff'

/** The study-area outline: warm over a photograph, cyan over the dark globe. */
const BOUNDARY = { survey: '#ffd54f', tactical: ACCENT }

export function boundaryColour(mode: ViewMode): Color {
  return Color.fromCssColorString(BOUNDARY[mode])
}

export interface ViewModeContext {
  /** Base imagery layer, so it can be dimmed rather than replaced. */
  baseLayer: ImageryLayer | null
  /** The scene bbox. */
  rectangle: Rectangle | null
}

/**
 * Apply `mode` to a viewer. Returns a disposer that restores what was there.
 *
 * A no-op for `survey`, which is the state the viewer is already in: on a survey
 * pass the previous tactical pass has already been cleaned up by React, so there
 * is nothing left to undo.
 */
export function applyViewMode(
  viewer: Viewer,
  mode: ViewMode,
  context: ViewModeContext,
): () => void {
  if (viewer.isDestroyed() || mode !== 'tactical') return () => undefined

  const { scene } = viewer
  const undo: Array<() => void> = []

  /** Record a setter so the previous value goes back exactly as it was found. */
  const swap = <T,>(read: () => T, write: (value: T) => void, value: T): void => {
    const previous = read()
    undo.unshift(() => write(previous))
    write(value)
  }

  // ---- the void behind everything ------------------------------------------
  swap(() => scene.backgroundColor, (v) => (scene.backgroundColor = v),
    Color.fromCssColorString('#02050b'))
  // `skyAtmosphere`, `skyBox` and `sun` are all optional on `Scene`, and a viewer
  // built without them still has to switch modes cleanly -- so the swap is
  // conditional rather than guarded by a non-null assertion that would throw on
  // exactly the viewer least able to afford it.
  if (scene.skyAtmosphere) {
    swap(() => scene.skyAtmosphere!.show, (v) => (scene.skyAtmosphere!.show = v), false)
  }
  if (scene.skyBox) {
    swap(() => scene.skyBox!.show, (v) => (scene.skyBox!.show = v), false)
  }
  // The sun stays on. It is tempting to kill it along with the sky, since a dark
  // globe wants no daylight -- but `czm_phong` drops every `ColorMaterialProperty`
  // in the scene to half brightness without one, which would quietly dim the
  // interventions, the boundary outline and anything a plan adds later. The
  // building shader zeroes `diffuse` precisely so that it does not care.
  if (scene.sun) {
    swap(() => scene.sun!.show, (v) => (scene.sun!.show = v), true)
  }
  swap(() => scene.fog.enabled, (v) => (scene.fog.enabled = v), false)
  swap(() => scene.globe.baseColor, (v) => (scene.globe.baseColor = v),
    Color.fromCssColorString('#04070e'))
  swap(() => scene.globe.showGroundAtmosphere, (v) => (scene.globe.showGroundAtmosphere = v), false)
  // Globe lighting would otherwise apply a day/night terminator over a scene that
  // no longer has a sky to explain it.
  swap(() => scene.globe.enableLighting, (v) => (scene.globe.enableLighting = v), false)

  // ---- the basemap, kept coloured under the slab --------------------------
  // The globe is clipped to the bbox, so this imagery only shows *under the
  // slab* — there is no photographic basemap bleeding past the neon buildings
  // to look like a mistake. Earlier the layer was dimmed and desaturated
  // (saturation 0.15) to force a near-monochrome instrument look; with the
  // surrounding map clipped away that is no longer wanted, so the floor is
  // kept at full colour and only slightly toned so the building edges still
  // read. Brightness stays a touch under 1.0 so the coloured map does not
  // out-shout the rim glow.
  const layer = context.baseLayer
  if (layer) {
    // Coloured floor, not the old near-monochrome (0.34 / 0.15), but kept
    // under the bloom threshold: at brightness 0.8+ the map itself caught the
    // bloom pass and the whole slab washed to white. 0.6 stays legible as a
    // real map without feeding the glow.
    swap(() => layer.brightness, (v) => (layer.brightness = v), 0.6)
    swap(() => layer.contrast, (v) => (layer.contrast = v), 1.1)
    swap(() => layer.saturation, (v) => (layer.saturation = v), 0.95)
    swap(() => layer.gamma, (v) => (layer.gamma = v), 1.15)
    swap(() => layer.hue, (v) => (layer.hue = v), 0.0)
  }

  // ---- the glow ------------------------------------------------------------
  // Bloom ships disabled in Cesium, so switching it on is additive rather than
  // something the user has to be told about. It hangs off the stage collection,
  // not off `Scene`, in this version.
  //
  // The stock-looking "high contrast = threshold" reading is wrong for this
  // stage: at contrast 210 the curve amplified *everything* bright — bodies and
  // map alike — and the whole slab read as one glowing white square. These
  // values are measured on the live scene, not derived: a low contrast with a
  // negative brightness keeps the halo on the rims only, which is the look the
  // mode wants ("buildings should glow, but not like this").
  const bloom = scene.postProcessStages.bloom
  swap(() => bloom.enabled, (v) => (bloom.enabled = v), true)
  if (bloom.uniforms) {
    const u = bloom.uniforms as Record<string, number | boolean>
    swap(() => u.contrast, (v) => (u.contrast = v), 4.5)
    swap(() => u.brightness, (v) => (u.brightness = v), -0.3)
    swap(() => u.delta, (v) => (u.delta = v), 1.0)
    swap(() => u.sigma, (v) => (u.sigma = v), 2.0)
    swap(() => u.stepSize, (v) => (u.stepSize = v), 1.0)
    swap(() => u.glowOnly, (v) => (u.glowOnly = v), false)
  }

  viewer.scene.requestRender()

  return () => {
    if (!viewer.isDestroyed()) {
      for (const restore of undo) restore()
      viewer.scene.requestRender()
    }
  }
}

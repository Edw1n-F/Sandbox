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
 * **No data, and no claim.** The tactical mode re-renders the *same* provisional
 * massing envelopes produced by the *same* generator. It is a different way of
 * looking at a provisional model, so the panel keeps the `provisional` chip in
 * both modes and the legend says what the ramp means. A prettier renderer is not
 * a better dataset, and this project has already been burned by that confusion.
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
  Cartesian2,
  Color,
  ConstantProperty,
  GridMaterialProperty,
  HeightReference,
  Rectangle,
  type ImageryLayer,
  type Viewer,
} from 'cesium'

import type { ViewMode } from '@/api/types'

export type { ViewMode }

/** UI copy. Kept next to the palette so the switch and the look cannot drift. */
export const VIEW_MODES: Record<ViewMode, { label: string; blurb: string }> = {
  survey: {
    label: 'Survey',
    blurb: 'Daylight basemap, lit envelopes. For reading the plan.',
  },
  tactical: {
    label: 'Tactical',
    blurb: 'Dark instrument view. Envelopes traced in neon, coloured by height.',
  },
}

/** Reference grid spacing, in metres. Also what the legend says. */
export const GRID_SPACING_M = 200

const ACCENT = '#3fe0ff'

/** The study-area outline: warm over a photograph, cyan over the dark globe. */
const BOUNDARY = { survey: '#ffd54f', tactical: ACCENT }

export function boundaryColour(mode: ViewMode): Color {
  return Color.fromCssColorString(BOUNDARY[mode])
}

/**
 * Height ramp endpoints and band count.
 *
 * These mirror the shader in `techMaterial.ts`, and the legend reads them, so the
 * two have to agree. Quoting rather than retyping is the only reason they cannot
 * drift apart silently.
 */
export const HEIGHT_RAMP = {
  low: '#0d4a78',
  high: '#ffb03a',
  bands: 6,
}

export interface ViewModeContext {
  /** Base imagery layer, so it can be dimmed rather than replaced. */
  baseLayer: ImageryLayer | null
  /** The scene bbox, for the reference grid. */
  rectangle: Rectangle | null
}

/** Rectangle size in metres, at the rectangle's own mid-latitude. */
function extentMetres(rectangle: Rectangle): { width: number; height: number } {
  const midLat = ((rectangle.north + rectangle.south) / 2) * (Math.PI / 180)
  return {
    width: Math.abs(rectangle.east - rectangle.west) * 111_320 * Math.cos(midLat),
    height: Math.abs(rectangle.north - rectangle.south) * 110_574,
  }
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

  // ---- the basemap, kept but pushed back -----------------------------------
  // Not removed: the wet-grid and heatmap overlays are georeferenced against this
  // imagery, and a flood map with no basemap under it is harder to place, not
  // easier. Dimmed and desaturated towards the accent hue instead, so it still
  // reads as terrain rather than as a photograph.
  const layer = context.baseLayer
  if (layer) {
    swap(() => layer.brightness, (v) => (layer.brightness = v), 0.34)
    swap(() => layer.contrast, (v) => (layer.contrast = v), 1.35)
    swap(() => layer.saturation, (v) => (layer.saturation = v), 0.15)
    swap(() => layer.gamma, (v) => (layer.gamma = v), 1.9)
    swap(() => layer.hue, (v) => (layer.hue = v), Math.PI / 0.55)
  }

  // ---- the glow ------------------------------------------------------------
  // Bloom ships disabled in Cesium, so switching it on is additive rather than
  // something the user has to be told about. It hangs off the stage collection,
  // not off `Scene`, in this version.
  //
  // `contrast` is the threshold, and it is the one number that decides whether
  // this looks like an instrument or like a smeared photograph: pushed up so
  // only the emissive rims cross it and the dimmed basemap does not.
  const bloom = scene.postProcessStages.bloom
  swap(() => bloom.enabled, (v) => (bloom.enabled = v), true)
  if (bloom.uniforms) {
    const u = bloom.uniforms as Record<string, number | boolean>
    // Tight, not wide. A broad sigma over 2,639 bright envelopes does not read as
    // glow, it reads as a cyan fog laid over the district — which is worse than no
    // bloom at all, because it flattens exactly the height ramp the mode exists to
    // show. These values keep the halo on the rim it belongs to.
    swap(() => u.contrast, (v) => (u.contrast = v), 210)
    swap(() => u.brightness, (v) => (u.brightness = v), -0.22)
    swap(() => u.delta, (v) => (u.delta = v), 0.8)
    swap(() => u.sigma, (v) => (u.sigma = v), 3.0)
    swap(() => u.stepSize, (v) => (u.stepSize = v), 1.0)
    swap(() => u.glowOnly, (v) => (u.glowOnly = v), false)
  }

  // ---- the reference grid --------------------------------------------------
  //
  // Bounded to the study area on purpose. Cesium's own `GridImageryProvider`
  // grids the entire globe, which at this zoom is either one coarse cross or a
  // moire across the horizon. A clamped rectangle gives an even lattice exactly
  // where the work is.
  if (context.rectangle) {
    const { width, height } = extentMetres(context.rectangle)
    const grid = viewer.entities.add({
      name: 'Reference grid',
      rectangle: {
        coordinates: context.rectangle,
        material: new GridMaterialProperty({
          color: Color.fromCssColorString(ACCENT).withAlpha(0.7),
          // `cellAlpha` is the *line* opacity despite the name — the grid shader has
          // no separate cell fill, it draws lines and scales them by this. Setting
          // it to 0, which reads like "cells off", makes the whole grid invisible.
          //
          // Kept low on purpose. Cesium draws this grid as geometry-space lines, so
          // at a grazing angle `fwidth` smears each one into a wide band — the
          // lattice stays legible head-on and turns into pale sheets across the
          // district when the camera is low. MapLibre does not have this problem
          // because its grid is screen-space. A reference grid that smears over the
          // data it exists to measure is worse than a faint one, so it is tuned to
          // stay readable where it is square-on and stay out of the way where not.
          cellAlpha: 0.3,
          // `lineCount` counts lines, not cells, so 12 x 17 lines across a
          // 2.37 x 3.32 km bbox is a 200 m lattice -- the number GRID_SPACING_M
          // names and the legend quotes.
          lineCount: new Cartesian2(
            Math.max(2, Math.round(width / GRID_SPACING_M)),
            Math.max(2, Math.round(height / GRID_SPACING_M)),
          ),
          lineThickness: new Cartesian2(1, 1),
        }),
        // Clamped, so the lattice drapes over 139 m of relief instead of
        // slicing through it at one fixed height.
        heightReference: new ConstantProperty(HeightReference.CLAMP_TO_GROUND),
        fill: true,
        outline: false,
      },
    })
    undo.unshift(() => {
      if (!viewer.isDestroyed()) viewer.entities.remove(grid)
    })
  }

  viewer.scene.requestRender()

  return () => {
    if (!viewer.isDestroyed()) {
      for (const restore of undo) restore()
      viewer.scene.requestRender()
    }
  }
}

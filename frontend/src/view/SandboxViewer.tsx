/**
 * Cesium `Viewer` container (spec §1.1 `view/SandboxViewer.tsx`).
 *
 * Narrow by design (CLAUDE.md: the rendering layer only "draws"): read state,
 * build providers, keep layers in sync, dispose. It never fetches.
 *
 * Disposal matters. Cesium holds WebGL contexts, workers and handlers that
 * React's unmount does not release, so a viewer left alive leaks on every
 * remount. StrictMode double-mounts in development, which is where that shows.
 */
import { useEffect, useRef } from 'react'
import { create } from 'zustand'
import {
  Cartesian2,
  Cartesian3,
  Cartesian4,
  ClippingPlane,
  ClippingPlaneCollection,
  ClassificationType,
  Color,
  Entity,
  ImageMaterialProperty,
  ConstantProperty,
  LabelStyle,
  Math as CesiumMath,
  Matrix4,
  Model,
  NearFarScalar,
  Rectangle,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Transforms,
  VerticalOrigin,
  Viewer,
  type ImageryLayer,
} from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import { addOnlineProps, type OnlinePropHandle } from './onlineProps'
import { add3DTileset, type TilesetHandle } from './tilesetLayer'
import { hk1980ToEcefMatrix } from './hk1980Transform'
import { addInterventions, type InterventionLayerHandle } from './interventionLayer'
import { deleteIntervention } from '@/api/planApi'
import { publishHeading } from './cameraHeading'
import { demElevationRange, sampleDemHeight, whenDemReady } from './demProvider'
import {
  createBaseImageryLayer,
  createOverlayLayer,
  createTerrainProvider,
  polygonToRectangle,
} from './terrainLoader'
import {
  bindOrbitGestures,
  DEFAULT_HEADING_DEG,
  DEFAULT_PITCH_DEG,
  frameExtent,
  orbitBy,
  setSandboxInset,
  setSandboxPivot,
  tuneNavigation,
  zoomBy,
  type Extent,
} from './cameraRig'
import { applyViewMode, boundaryColour } from './viewModes'
import booleanIntersects from '@turf/boolean-intersects'
import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson'
import {
  collectIntersectingIds,
  createFootprintLayer,
  type BuildingProps,
  type FootprintLayerHandle,
} from './footprintLayer'

let propsHandle: OnlinePropHandle | null = null
let tilesetHandle: TilesetHandle | null = null
let terrainTilesetHandle: TilesetHandle | null = null
let mtrModel: Model | null = null
let mtrMarker: Entity | null = null
let footprintLayer: FootprintLayerHandle | null = null

/**
 * The planned project whose 3D model this viewer draws.
 *
 * Kept as a constant because the GLB and its footprint sidecar are single
 * assets on the scene record, not a per-project lookup. When a second modelled
 * project lands this becomes a map from project id to model URL, which is why
 * the id is named here rather than inlined at the check.
 */
const STATION_PROJECT_ID = 'mtr-station'

/**
 * The boxes a planned project stands on, recomputed with the 2.5D layer.
 *
 * A module-level `let` for the same reason `footprintLayer` is one: the footprint
 * effect and the selection effect are separate, and the second has to read what
 * the first computed. `EMPTY_IDS` is shared and never mutated, so "nothing
 * hidden" is one value rather than a new Set on every render.
 */
let stationHiddenIds: ReadonlySet<string> = new Set<string>()
const EMPTY_IDS: ReadonlySet<string> = new Set<string>()

const useProps = create<{ rev: number }>(() => ({ rev: 0 }))

/**
 * A viewer is only usable while it exists and has not been destroyed.
 *
 * React StrictMode mounts, unmounts and remounts every effect in development.
 * The create-effect's cleanup destroys the viewer, and the *other* effects'
 * cleanups then run against that destroyed instance -- `viewer.imageryLayers`
 * reaches `Viewer.scene`, which dereferences an internal that `destroy()` has
 * already released, and throws. Every touch of the viewer goes through here.
 */
function isLive(viewer: Viewer | null): viewer is Viewer {
  return viewer !== null && !viewer.isDestroyed()
}

/**
 * Records why an overlay is not on screen, so the panel can say "failed" rather
 * than leaving a switch that silently does nothing.
 */
function setOverlayStatus(key: 'wetGrid' | 'heatmap', status: string): void {
  useOverlayStatus.setState((s) => ({ ...s, [key]: status }))
}

/** Overlay load status, keyed by layer. Read by the layer pills in `ViewButtons`. */
export const useOverlayStatus = create<Record<'wetGrid' | 'heatmap', string>>(() => ({
  wetGrid: 'idle',
  heatmap: 'idle',
}))

/** Camera actions, callable from `ViewButtons` without prop drilling. */
export const cameraControl: {
  /** Frame the bbox in 3D. The default, and what the app opens on. */
  flyToBbox: () => void
  /** Straight down. For reading the wet-grid and heatmap rasters. */
  topDown: () => void
  reset: () => void
  /** Orbit about whatever the camera is looking at. Degrees, relative. */
  orbit: (headingDeg: number, pitchDeg: number) => void
  /** Move towards (<1) or away from (>1) the subject. */
  zoom: (factor: number) => void
} = {
  flyToBbox: () => undefined,
  topDown: () => undefined,
  reset: () => undefined,
  orbit: () => undefined,
  zoom: () => undefined,
}

/**
 * Steps for the on-screen buttons and the keyboard.
 *
 * `ZOOM_STEP` is a distance multiplier per press, so lower is more aggressive: 0.8
 * moved the camera a fifth of the way per press, which on a 3 km district felt like
 * nothing happening. 0.55 is roughly a third per press, which is one press to make a
 * visible difference and about four to cross the whole district.
 */
const ORBIT_STEP_DEG = 12
const TILT_STEP_DEG = 8
const ZOOM_STEP = 0.55

/**
 * Panel width, in CSS pixels, occupied on the left of the canvas.
 *
 * A module-level value rather than a prop so `frameExtent` can shift the study
 * area out from behind the drawer without threading the panel's layout through
 * the camera code. `App` is the only writer.
 */
let panelInsetLeft = 0

export function setPanelInsetLeft(px: number): void {
  panelInsetLeft = px
  setSandboxInset(px)
}

export interface SandboxViewerProps {
  /** Leftover for callers that need to size the viewer; the canvas is 100%. */
  className?: string
}

export default function SandboxViewer({ className }: SandboxViewerProps = {}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const baseRef = useRef<ImageryLayer | null>(null)
  // Each entry also records the URL its provider was built from: a live run
  // writes a new overlay PNG and hands the store a new URL, and a provider
  // built from the old URL would keep showing the old texture forever.
  // Overlays draped over the 3D tiles are entities, not imagery layers: an imagery layer
  // paints on the globe, which sits UNDER the tile meshes, so the overlay was hidden by
  // the very surface it describes. A classification rectangle is drawn on whatever
  // geometry is there, so it follows buildings and terrain and does not care how the
  // tile meshes were cleaned or decimated.
  const drapedRef = useRef<Record<'wetGrid' | 'heatmap', { id: string; url: string } | null>>({
    wetGrid: null,
    heatmap: null,
  })
  const overlaysRef = useRef<Record<'wetGrid' | 'heatmap', { layer: ImageryLayer; url: string } | null>>({
    wetGrid: null,
    heatmap: null,
  })
  const boundaryRef = useRef<string | null>(null)

  const bbox = useSceneStore((s) => s.scene?.bbox ?? null)
  const terrainMeta = useSceneStore((s) => s.terrain)
  const interventions = usePlanStore((s) => s.interventions)
  const showInterventions = useSceneStore((s) => s.layers.interventions)
  const showProps = useSceneStore((s) => s.layers.props ?? true)
  const showRealBuildings = useSceneStore((s) => s.layers.realBuildings ?? true)
  const showMassing25d = useSceneStore((s) => s.layers.massing25d ?? false)
  const selectedPlanIds = usePlanStore((s) => s.selectedPlanIds)
  const plans = usePlanStore((s) => s.plans)
  /**
   * Whether the MTR station project is active.
   *
   * Read from the run's selection rather than a layer flag, so the station is
   * visible exactly when the run scores it. Two sources of truth for "is the
   * station on" is how the picture and the number come to disagree.
   *
   * The selection holds plan **uuids**, not project ids: `create_plan` mints a
   * uuid and the seeder tags the plan with `params.projectId`. Comparing the
   * selection against the project id directly therefore never matches, which is
   * a bug this line had on its first draft -- the station appeared in the panel
   * as selected and nothing moved on the map. Resolve through the plan list.
   */
  const stationPlanId = plans.find(
    (p) => (p.params as { projectId?: string } | undefined)?.projectId === STATION_PROJECT_ID,
  )?.planId
  const showStation = Boolean(stationPlanId) && selectedPlanIds.includes(stationPlanId!)
  /**
   * The same value, for the footprint effect to read without depending on it.
   *
   * Depending on the selection would tear the layer down and refetch the building
   * list on every project toggle, and the rebuild races the effect below that
   * applies the hidden set. The selection is applied by `setHiddenIds` instead,
   * which is what that method is for.
   */
  const showStationRef = useRef(showStation)
  showStationRef.current = showStation
  const setPlanProjectLoading = useSceneStore((s) => s.setPlanProjectLoading)
  const layers = useSceneStore((s) => s.layers)
  const viewMode = useSceneStore((s) => s.viewMode)

  // ---- create the viewer once -------------------------------------------
  useEffect(() => {
    const container = containerRef.current
    if (!container || viewerRef.current) return

    // baseLayer:false — base imagery is added explicitly from OpenStreetMap, so
    // no Cesium Ion token is ever required (ADR-033).
    const viewer = new Viewer(container, {
      baseLayer: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      animation: false,
      timeline: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      requestRenderMode: true,
    })
    viewerRef.current = viewer

    // Dev-only handles on the live viewer and the two Cesium types needed to
    // rebuild the building fabric outside the app. They are what makes camera
    // behaviour and generated GLSL checkable from headless Chromium: the run
    // asserts on camera position after a pan, which is the only way to tell "the
    // drag moved the camera" from "the drag was swallowed and the view just
    // re-rendered", and the custom fabric only exists once Cesium has assembled it.
    if (import.meta.env.DEV) {
      const w = window as unknown as {
        __cesiumViewer?: Viewer
        __cesium?: { Color: typeof Color }
      }
      w.__cesiumViewer = viewer
      w.__cesium = { Color }
    }

    tuneNavigation(viewer)
    bindOrbitGestures(viewer)
    cameraControl.flyToBbox = () => flyTo(viewer, 'oblique')
    cameraControl.topDown = () => flyTo(viewer, 'nadir')
    // Reset = the opening oblique framing, not the scene's stored defaultCamera
    // (heading 0 / pitch -48 / 4,200 m): that one is a near-flat north view
    // that, with the slab's relief, reads as "the box disappeared". Re-framing
    // to the oblique view is what "reset framing" actually means here.
    cameraControl.reset = () => flyTo(viewer, 'oblique')
    cameraControl.orbit = (headingDeg, pitchDeg) => orbitBy(viewer, headingDeg, pitchDeg)
    cameraControl.zoom = (factor) => zoomBy(viewer, factor)

    // Publish the heading for the compass. `changed` fires only once the camera
    // has moved by `percentageChanged` of its view, so it is lowered from the 0.5
    // default: at 0.5 a slow rotation published nothing until it was far off.
    // `isLive` guards the callback because StrictMode can destroy the viewer
    // while a change event is still queued.
    viewer.camera.percentageChanged = 0.01
    const onCameraChanged = () => {
      if (!isLive(viewer)) return
      publishHeading(CesiumMath.toDegrees(viewer.camera.heading))
    }
    viewer.camera.changed.addEventListener(onCameraChanged)
    viewer.camera.moveEnd.addEventListener(onCameraChanged)

    return () => {
      viewer.camera.changed.removeEventListener(onCameraChanged)
      viewer.camera.moveEnd.removeEventListener(onCameraChanged)
      overlaysRef.current = { wetGrid: null, heatmap: null }
      drapedRef.current = { wetGrid: null, heatmap: null }
      baseRef.current = null
      boundaryRef.current = null
      viewerRef.current = null
      // destroy() releases the WebGL context, workers and imagery providers.
      if (!viewer.isDestroyed()) viewer.destroy()
    }
  }, [])

  // ---- terrain + base imagery -------------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer) || !terrainMeta) return

    let cancelled = false

    void createTerrainProvider(terrainMeta, bbox).then((binding) => {
      // The viewer may have been replaced (StrictMode remount) while the DEM
      // image was in flight; applying it to a dead viewer is the crash guarded
      // against by isLive().
      if (cancelled || !isLive(viewer)) return
      viewer.terrainProvider = binding.terrainProvider
      // Clip the globe to the study-area slab: only the sandbox shows, the
      // surrounding map is removed and the dark scene background reads as the
      // void the slab floats in. The slab's own floor is the base imagery,
      // which the view mode now keeps coloured (not desaturated) so the map
      // under the box reads as terrain. No walls: the clip is a hard vertical
      // cut at the bbox, with the terrain's own relief as the slab edge.
      clipGlobeToBBox(viewer, bbox)
      useSceneStore.setState((s) => ({
        scene: s.scene
          ? {
              ...s.scene,
              terrainIsReal: binding.terrainIsReal,
              terrainNote: binding.terrainNote,
            }
          : s.scene,
      }))
      // Re-frame once the terrain is real.
      //
      // The camera was fitted to the bbox while the surface was still flat, so
      // a study area with 141 m of relief leaves the camera *inside* a hill and
      // the globe renders black. Fitting again against the loaded terrain is the
      // fix, and it has to happen here rather than at init because the terrain
      // arrives asynchronously.
      if (binding.terrainIsReal) {
        flyTo(viewer, 'oblique', 0)
        viewer.scene.requestRender()
      }
      // Grounding is *not* done here. The real 3D Tiles buildings carry their
      // own heights; no procedural massing needs a DEM sample.
    })

    // When the 3D terrain tiles are present they are the map floor. Drawing the
    // flat OSM imagery on the globe underneath them z-fights with the mesh, so
    // the base layer is skipped and the globe is left as a dark, untextured
    // surface (it still carries the wet-grid / heat-map overlays and the
    // elevation used for grounding).
    const hasTerrainTiles = Boolean(terrainMeta.terrain3DUrl)
    const base = hasTerrainTiles ? null : createBaseImageryLayer(terrainMeta)
    if (base) viewer.imageryLayers.add(base)
    baseRef.current = base
    viewer.scene.globe.baseColor = hasTerrainTiles ? Color.fromCssColorString('#0b0f14') : Color.BLUE

    return () => {
      cancelled = true
      // Guarded: under StrictMode this cleanup can run after destroy().
      if (base && isLive(viewer) && viewer.imageryLayers.contains(base)) {
        viewer.imageryLayers.remove(base, false)
      }
      baseRef.current = null
    }
    // `bbox` is in the deps as well as `terrainMeta`: both change together when a
    // different scene is loaded, and the imagery has to be re-added against it.
  }, [terrainMeta, bbox])

  // ---- boundary outline --------------------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return
    const rectangle = polygonToRectangle(bbox)
    const outline = boundaryColour(viewMode).withAlpha(0.95)

    if (!rectangle) {
      if (boundaryRef.current) {
        const existing = viewer.entities.getById(boundaryRef.current)
        if (existing) viewer.entities.remove(existing)
        boundaryRef.current = null
      }
      return
    }
    if (boundaryRef.current) {
      const existing = viewer.entities.getById(boundaryRef.current)
      // Repainted rather than recreated, so switching mode does not drop the
      // outline and flash. Yellow reads on a photograph; cyan reads on the dark
      // globe, which is why it is mode-dependent at all.
      if (existing) {
        existing.show = layers.boundary
        if (existing.rectangle) {
          existing.rectangle.outlineColor = new ConstantProperty(outline)
        }
      }
      return
    }
    const id = viewer.entities.add({
      name: 'Study area boundary',
      rectangle: {
        coordinates: rectangle,
        // RectangleGraphics members are Property objects, not raw booleans.
        fill: new ConstantProperty(false),
        outline: new ConstantProperty(true),
        outlineColor: new ConstantProperty(outline),
        outlineWidth: new ConstantProperty(2),
      },
      show: layers.boundary,
    })
    boundaryRef.current = id.id
  }, [bbox, layers.boundary, viewMode])

  // ---- the view mode -----------------------------------------------------
  //
  // Keyed on `terrainMeta` as well as the mode, because it dims the base imagery
  // layer, and that layer is created by the effect above. Ordering matters more
  // than the dependency: this is declared after it, so on a first paint the
  // layer already exists.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer) || !terrainMeta) return

    const dispose = applyViewMode(viewer, viewMode, {
      baseLayer: baseRef.current,
      rectangle: polygonToRectangle(terrainMeta.bbox),
    })
    return dispose
  }, [viewMode, terrainMeta])

  // ---- overlay layers follow the toggles --------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer) || !terrainMeta) return
    const rectangle = polygonToRectangle(terrainMeta.bbox)
    if (!rectangle) return

    const wanted: Array<['wetGrid' | 'heatmap', string | null | undefined, number]> = [
      ['wetGrid', terrainMeta.wetGridUrl, 0.75],
      ['heatmap', terrainMeta.heatmapUrl, 0.6],
    ]

    let cancelled = false

    // Drop a layer without pulling its textures out from under a frame that is
    // still rendering them. `remove(layer, true)` destroys immediately, and when
    // a Run swaps overlays the destroy landed mid-render -- the scene then died
    // with "This object was destroyed" and the whole viewer went dark. Detach
    // now, destroy once the frames that could reference it have passed.
    const retireLayer = (layer: ImageryLayer) => {
      viewer.imageryLayers.remove(layer, false)
      setTimeout(() => {
        try {
          layer.destroy()
        } catch {
          // Already destroyed by a viewer teardown; nothing to do.
        }
      }, 2000)
    }

    const drape = Boolean(terrainMeta.terrain3DUrl || terrainMeta.buildings3DUrl || terrainMeta.buildings3dUrl)
    if (drape) {
      for (const [key, url, alpha] of wanted) {
        const cur = drapedRef.current[key]
        const want = layers[key] && Boolean(url)
        if (cur && (!want || cur.url !== url)) {
          const ent = viewer.entities.getById(cur.id)
          if (ent) viewer.entities.remove(ent)
          drapedRef.current[key] = null
        }
        if (!want || drapedRef.current[key]) continue
        const ent = viewer.entities.add({
          name: key === 'wetGrid' ? 'Flood extent' : 'Heat map overlay',
          rectangle: {
            coordinates: rectangle,
            material: new ImageMaterialProperty({
              image: url as string,
              transparent: true,
              color: Color.WHITE.withAlpha(Math.min(1, alpha + 0.2)),
            }),
            classificationType: ClassificationType.BOTH,
          },
        })
        drapedRef.current[key] = { id: ent.id, url: url as string }
        setOverlayStatus(key, 'ready')
      }
      viewer.scene.requestRender()
      return () => {
        cancelled = true
      }
    }

    for (const [key, url, alpha] of wanted) {
      let existing = overlaysRef.current[key]
      if (existing && url && existing.url !== url) {
        // A run produced a new overlay: drop the stale provider so the new
        // PNG is fetched, not just re-shown.
        retireLayer(existing.layer)
        overlaysRef.current[key] = null
        existing = null
      }
      const shouldShow = layers[key] && Boolean(url)
      // Narrowed here, not inside the async callback: TypeScript does not carry
      // the `Boolean(url)` check into a closure that runs later.
      const urlFor = url as string
      if (existing) {
        // Toggling visibility is cheap; rebuilding the provider re-downloads.
        existing.layer.show = shouldShow
        if (shouldShow && isLive(viewer)) viewer.scene.requestRender()
        continue
      }
      if (!shouldShow) continue

      // The provider resolves only once its image has loaded; see the note on
      // createOverlayLayer. Await it, then re-check that this viewer is still
      // the live one and that the layer is still wanted -- the user can toggle
      // off, or StrictMode can remount, while the PNG is in flight.
      void createOverlayLayer(url, rectangle, alpha)
        .then((layer) => {
          if (!layer || cancelled) return
          const live = viewerRef.current
          if (!isLive(live) || live !== viewer) return
          if (!useSceneStore.getState().layers[key]) return
          live.imageryLayers.add(layer)
          overlaysRef.current[key] = { layer, url: urlFor }
          // requestRenderMode is on, so a layer whose texture finished loading
          // asynchronously will never paint until something asks for a frame.
          // This is the single most common reason an overlay silently does
          // nothing in Cesium.
          live.scene.requestRender()
          setOverlayStatus(key, 'ready')
        })
        .catch((err: unknown) => {
          console.warn(`[sandbox] ${key} overlay failed to load:`, err)
          setOverlayStatus(key, 'failed')
        })
    }

    return () => {
      cancelled = true
    }
  }, [layers, terrainMeta])

  // ---- initial camera placement -----------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return
    flyTo(viewer, 'oblique', 0)
    // Placement happens once per scene load, which is what keying on `bbox` means. The
    // original disable comment here was stale -- the rule does not fire on this effect --
    // and a directive that suppresses nothing is worse than none, because it reads as
    // a suppression that is still doing something.
  }, [bbox])

  // ---- keyboard orbit ------------------------------------------------------
  //
  // Arrow keys orbit and tilt, +/- zoom. Not a substitute for the mouse: it is here
  // because rotating the view is the one navigation action a trackpad does badly
  // (right-drag is a two-finger click on a Mac, and middle-drag is not available at
  // all), and because a keyboard route is the only one that works without a pointer.
  //
  // ## Which keys are left alone, and why it is not "anything in the panel"
  //
  // The rainfall and temperature controls are MUI sliders, and arrow keys are how a
  // slider is operated, so those must be spared. An earlier version guarded the whole
  // drawer instead, which was both too broad and wrong in a way nobody would have
  // reported: after clicking any camera button, focus sat on that button and the
  // arrow keys stopped orbiting until the user clicked the map. Buttons do not
  // consume arrow keys, so the guard names only what genuinely handles them —
  // text entry, sliders, and listboxes, where arrows move a selection.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isLive(viewer)) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
      if (target?.closest('[role="slider"], [role="listbox"], [role="combobox"]')) return

      switch (event.key) {
        case 'ArrowLeft':
          orbitBy(viewer, -ORBIT_STEP_DEG, 0)
          break
        case 'ArrowRight':
          orbitBy(viewer, ORBIT_STEP_DEG, 0)
          break
        case 'ArrowUp':
          // Arrow up raises the view, which means pitching towards the horizon.
          // Pitch is negative-when-looking-down, so this adds to it.
          orbitBy(viewer, 0, TILT_STEP_DEG)
          break
        case 'ArrowDown':
          orbitBy(viewer, 0, -TILT_STEP_DEG)
          break
        case '+':
        case '=':
          zoomBy(viewer, ZOOM_STEP)
          break
        case '-':
        case '_':
          zoomBy(viewer, 1 / ZOOM_STEP)
          break
        // Home and 0 re-frame, T goes top-down. These are the two jumps that
        // arrows and +/- cannot reach, and they are what a lost user wants most.
        case 'Home':
        case '0':
          cameraControl.reset()
          break
        case 't':
        case 'T':
          cameraControl.topDown()
          break
        default:
          return
      }
      event.preventDefault()
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // ---- ask for a frame when terrain and imagery finish --------------------
  //
  // `requestRenderMode` means nothing paints unless something requests a frame, and
  // imagery arriving from the network does not request one. The symptom is that
  // flying to a new zoom leaves the basemap blurry at the wrong level of detail
  // until some unrelated interaction happens to nudge a frame -- which reads as a
  // broken basemap rather than as a rendering-mode detail, and is most obvious on
  // exactly the interaction this view was built for.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return
    const globe = viewer.scene.globe
    const onTileLoad = () => {
      // Only once the queue has drained, so this is one render per settled batch
      // rather than one per tile.
      if (!viewer.isDestroyed() && globe.tilesLoaded) viewer.scene.requestRender()
    }
    globe.tileLoadProgressEvent.addEventListener(onTileLoad)
    return () => {
      if (!viewer.isDestroyed()) {
        globe.tileLoadProgressEvent.removeEventListener(onTileLoad)
      }
    }
  }, [])

  // ---- online 3D assets ---------------------------------------------------
  //
  // Loaded once, from GitHub raw, with no token and no proxy. Failure is
  // recorded rather than thrown so a dead asset URL cannot empty the scene, and
  // the loaded count is reported so a silent failure is still visible.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return

    let cancelled = false
    void addOnlineProps(viewer).then((handle) => {
      if (cancelled) {
        handle?.dispose()
        return
      }
      propsHandle = handle
      useProps.setState({ rev: useProps.getState().rev + 1 })
    })

    return () => {
      cancelled = true
      propsHandle?.dispose()
      propsHandle = null
    }
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer) || !propsHandle) return
    for (const model of propsHandle.models.values()) model.show = showProps
    viewer.scene.requestRender()
  }, [showProps])

  // ---- 3D Tiles real buildings (6-NW-12D textured meshes) ----------------
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.buildings3DUrl ?? terrainMeta?.buildings3dUrl
    if (!isLive(viewer) || !url) {
      tilesetHandle = null
      return
    }

    let cancelled = false
    void add3DTileset(viewer, url, { modelMatrix: hk1980ToEcefMatrix(), maximumScreenSpaceError: 24, name: 'buildings' }).then((handle) => {
      if (cancelled || !handle) return
      tilesetHandle = handle
      // While the 2.5D switch is on, the footprint overlay replaces this layer.
      handle.tileset.show = showRealBuildings && !showMassing25d
      viewer.scene.requestRender()
    })

    return () => {
      cancelled = true
      tilesetHandle?.dispose()
      tilesetHandle = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terrainMeta?.buildings3DUrl, terrainMeta?.buildings3dUrl])

  useEffect(() => {
    if (!tilesetHandle) return
    tilesetHandle.tileset.show = showRealBuildings && !showMassing25d
    viewerRef.current?.scene.requestRender()
  }, [showRealBuildings, showMassing25d])

  // ---- 3D Tiles terrain visual mesh (6-NW-12D terrain(TB)) ----------------
  // Loaded as a Primitive alongside the globe TerrainProvider.  The globe
  // provider stays as the clamping / elevation-sampling source; terrain(TB) is
  // the visual surface only.
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.terrain3DUrl
    if (!isLive(viewer) || !url) {
      terrainTilesetHandle = null
      return
    }

    let cancelled = false
    void add3DTileset(viewer, url, { modelMatrix: hk1980ToEcefMatrix(), maximumScreenSpaceError: 24, name: 'terrain' }).then((handle) => {
      if (cancelled || !handle) return
      terrainTilesetHandle = handle
      // Frame and orbit about the centre of the whole sandbox terrain, not the corner
      // or whatever the screen centre happens to hit. Re-frame now that it is known so
      // the default view is calibrated against the real tile extent.
      setSandboxPivot(handle.tileset.boundingSphere.center)
      flyTo(viewer, 'oblique', 0)
      viewer.scene.requestRender()
    })

    return () => {
      cancelled = true
      setSandboxPivot(null)
      terrainTilesetHandle?.dispose()
      terrainTilesetHandle = null
    }
  }, [terrainMeta?.terrain3DUrl])

  // ---- intervention features ---------------------------------------------
  //
  // The discrete half of ADR-032: interventions are geometry, not raster, so
  // they go in through GeoJsonDataSource and can be extruded and picked. The
  // effect is keyed on the *identity* of the collection rather than the object,
  // because `planStore` replaces the object on every fetch and an effect keyed
  // on the reference would tear down and rebuild the data source each time even
  // when the features are identical.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return

    // `massing_height` features drove the procedural city-model pillar layer,
    // which has been removed. They are not handed to the real-building viewer.
    const drawn = interventions.features.filter(
      (f) => f.properties?.interventionType !== 'massing_height',
    )

    let disposed = false
    let handle: InterventionLayerHandle | undefined
    let clickHandler: ScreenSpaceEventHandler | undefined

    void addInterventions(viewer, {
      type: 'FeatureCollection',
      features: showInterventions ? drawn : [],
    }).then((h) => {
      // The effect may have been torn down while the GeoJSON was in flight.
      if (disposed) {
        h.dispose()
        return
      }
      handle = h

      if (!isLive(viewer)) return
      const scene = viewer.scene
      const planId = usePlanStore.getState().activePlanId
      if (!planId) return

      clickHandler = new ScreenSpaceEventHandler(scene.canvas)
      clickHandler.setInputAction((event: ScreenSpaceEventHandler.PositionedEvent) => {
        if (!isLive(viewer)) return
        const picked = scene.pick(event.position)
        const entity = picked?.id
        const interventionId = h.interventionIdOf(entity)
        if (!interventionId) return
        if (!window.confirm('Delete this intervention? This cannot be undone.')) return
        void (async () => {
          try {
            await deleteIntervention(planId, interventionId)
          } catch (err) {
            console.warn('[sandbox] delete intervention failed:', err)
            return
          }
          // Re-fetch so the layer, the store and the server all agree.
          const current = usePlanStore.getState().activePlanId
          if (current) await usePlanStore.getState().selectPlan(current)
        })()
      }, ScreenSpaceEventType.LEFT_CLICK)
    })

    return () => {
      disposed = true
      if (clickHandler && !clickHandler.isDestroyed()) clickHandler.destroy()
      handle?.dispose()
    }
  }, [showInterventions, interventions])

  // ---- MTR Plan Project GLB model ----------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.planProjectUrl
    if (!isLive(viewer) || !url || !showStation) {
      if (mtrModel && !mtrModel.isDestroyed()) {
        viewer?.scene.primitives.remove(mtrModel)
        mtrModel = null
      }
      return
    }

    console.warn('[planProject] loading GLB:', url)
    setPlanProjectLoading(true)
    let cancelled = false
    // Confirmed inside scene bbox (113.9922–113.9994 E, 22.431–22.4363 N).
    // Real station (22°25′55″N 113°59′17″E) is ~400 m west of the scene boundary,
    // so we place the model at the cluster of buildings the station would replace.
    const MTR_LON = 113.9930
    const MTR_LAT = 22.4360
    const MTR_SCALE = 50

    void whenDemReady().then(async () => {
      if (cancelled) return
      const terrainM = sampleDemHeight(MTR_LON, MTR_LAT) ?? 11.3
      // The GLB has a baked -90° X rotation (Blender Y-up export convention).
      // After Cesium applies it, the visual base of the model aligns with the
      // ENU origin (Z=0 in local frame). Place the origin directly at terrain
      // height — no vertical offset needed.
      const originH = terrainM
      const labelH  = terrainM + (0.45 + 0.48) * MTR_SCALE + 5   // above model top
      console.warn(`[planProject] terrain=${terrainM.toFixed(1)} m, origin=${originH.toFixed(1)} m`)

      const model = await Model.fromGltfAsync({
        url,
        modelMatrix: Transforms.eastNorthUpToFixedFrame(
          Cartesian3.fromDegrees(MTR_LON, MTR_LAT, originH),
        ),
        scale: MTR_SCALE,
      })
      if (cancelled || !isLive(viewerRef.current)) { model.destroy(); return }
      const v = viewerRef.current!
      mtrModel = model
      v.scene.primitives.add(model)

      mtrMarker = v.entities.add({
        position: Cartesian3.fromDegrees(MTR_LON, MTR_LAT, labelH),
        label: {
          text: '洪水桥站\nMTR Hung Shui Kiu',
          font: 'bold 14px sans-serif',
          style: LabelStyle.FILL_AND_OUTLINE,
          fillColor: Color.WHITE,
          outlineColor: Color.fromCssColorString('#0a2e1a'),
          outlineWidth: 3,
          verticalOrigin: VerticalOrigin.BOTTOM,
          pixelOffset: new Cartesian2(0, -10),
          scaleByDistance: new NearFarScalar(500, 1.0, 3000, 0.75),
          translucencyByDistance: new NearFarScalar(3000, 1.0, 8000, 0.0),
          showBackground: true,
          backgroundColor: Color.fromCssColorString('#0d4a2c').withAlpha(0.82),
          backgroundPadding: new Cartesian2(8, 5),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        point: {
          pixelSize: 14,
          color: Color.fromCssColorString('#5cff9d'),
          outlineColor: Color.fromCssColorString('#0a2e1a'),
          outlineWidth: 2,
          scaleByDistance: new NearFarScalar(300, 1.4, 5000, 0.6),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      })

      void v.camera.flyTo({
        destination: Cartesian3.fromDegrees(MTR_LON, MTR_LAT - 0.004, labelH + 300),
        orientation: {
          heading: CesiumMath.toRadians(0),
          pitch: CesiumMath.toRadians(-25),
          roll: 0,
        },
        duration: 2.0,
      })

      v.scene.requestRender()
      setPlanProjectLoading(false)
      console.warn('[planProject] GLB loaded and visible')
    }).catch((err) => {
      setPlanProjectLoading(false)
      console.error('[planProject] GLB failed to load:', err)
    })

    return () => {
      cancelled = true
      setPlanProjectLoading(false)
      if (mtrModel && !mtrModel.isDestroyed()) {
        viewerRef.current?.scene.primitives.remove(mtrModel)
        mtrModel = null
      }
      if (mtrMarker) {
        viewerRef.current?.entities.remove(mtrMarker)
        mtrMarker = null
      }
    }
  }, [terrainMeta?.planProjectUrl, showStation, setPlanProjectLoading])

  // ---- 2.5D footprint overlay (Plan Project companion) --------------------
  // Draw the city as 2.5D white massing in place of the textured 3D Tiles.
  //
  // This is the building *style* and nothing else: whether a planned project is
  // present is a separate question, answered by the run's selection. The set of
  // boxes a project covers is computed here because it needs the building list,
  // and stashed in `stationHiddenIds` for the effect below to apply.
  useEffect(() => {
    const viewer = viewerRef.current
    const buildingsUrl = terrainMeta?.buildingsUrl
    const footprintUrl = terrainMeta?.planProjectFootprintUrl
    if (!isLive(viewer) || !showMassing25d || !buildingsUrl || !footprintUrl) {
      footprintLayer?.destroy()
      footprintLayer = null
      stationHiddenIds = new Set()
      return
    }

    let cancelled = false
    void (async () => {
      try {
        // DEM must be ready before createFootprintLayer samples ground heights
        // for each footprint, otherwise every box sinks into the terrain.
        await whenDemReady()
        if (cancelled || !isLive(viewerRef.current)) return
        const [buildings, footprint] = await Promise.all([
          fetch(buildingsUrl).then((r) => r.json() as Promise<FeatureCollection<Polygon | MultiPolygon, BuildingProps>>),
          fetch(footprintUrl).then((r) => r.json() as Promise<Feature<Polygon> | Polygon>),
        ])
        if (cancelled || !isLive(viewerRef.current)) return
        stationHiddenIds = collectIntersectingIds(buildings, footprint, (a, b) =>
          booleanIntersects(a as never, b as never),
        )
        footprintLayer?.destroy()
        // Built with the project's boxes hidden or shown according to the
        // current selection, so the layer is never briefly wrong. Read through
        // the ref: `showStation` is deliberately NOT a dependency of this effect
        // (see below), so the prop value here would be a stale closure.
        footprintLayer = createFootprintLayer(
          viewerRef.current,
          buildings,
          showStationRef.current ? stationHiddenIds : EMPTY_IDS,
        )
        console.warn(
          `[planProject] 2.5D overlay: ${footprintLayer.size} boxes, ${stationHiddenIds.size} under the project footprint`,
        )
      } catch (err) {
        console.error('[planProject] 2.5D overlay failed:', err)
      }
    })()

    return () => {
      cancelled = true
      footprintLayer?.destroy()
      footprintLayer = null
    }
  }, [showMassing25d, terrainMeta?.buildingsUrl, terrainMeta?.planProjectFootprintUrl])

  // Hide the boxes a planned project stands on -- only while that project is
  // actually on. With the project off there is nothing to replace them with, and
  // a hole in the massing reads as missing data rather than as a design choice.
  //
  // A separate effect rather than a rebuild: `setHiddenIds` toggles 390 entities
  // in place, where recreating the layer would tear down and refetch.
  useEffect(() => {
    footprintLayer?.setHiddenIds(showStation ? stationHiddenIds : EMPTY_IDS)
    viewerRef.current?.scene.requestRender()
  }, [showStation])

  return <div ref={containerRef} className={className ?? 'absolute inset-0 z-0'} />
}

/** Which of the three framings a camera button asked for. */
type Frame = 'oblique' | 'nadir' | 'scene'

/**
 * Put the camera somewhere deliberate.
 *
 * `'nadir'` is the only mode that still frames by handing Cesium a `Rectangle`,
 * and it is kept as an explicit button rather than as the default because a
 * rectangle has no orientation: Cesium can only ever fit it from directly
 * overhead. That is correct for reading a raster and wrong for a city, so the
 * app opens in `'oblique'` and the flat view is something the user chooses on
 * purpose. See `cameraRig.ts`.
 */
function flyTo(viewer: Viewer, frame: Frame, duration = 1.2): void {
  if (!isLive(viewer)) return
  const store = useSceneStore.getState()
  const scene = store.scene
  if (!scene) return

  const rectangle = polygonToRectangle(scene.bbox)

  if (frame !== 'scene' && rectangle) {
    // The DEM's own relief, read rather than typed: it decides how much vertical
    // extent the frame has to keep, and a hard-coded 141 m would be a constant
    // that goes stale the next time the DEM is re-fetched over another bbox.
    const relief = demElevationRange()
    frameExtent(viewer, extentOf(rectangle), {
      insetLeft: panelInsetLeft,
      reliefM: relief ? Math.max(relief.max, 0) : 0,
      pitchDeg: frame === 'nadir' ? -90 : DEFAULT_PITCH_DEG,
      headingDeg: DEFAULT_HEADING_DEG,
      duration,
    })
    return
  }

  const lon = scene.defaultCamera.longitude ?? scene.center.coordinates[0]
  const lat = scene.defaultCamera.latitude ?? scene.center.coordinates[1]
  viewer.camera.flyTo({
    destination: Cartesian3.fromDegrees(lon, lat, Math.max(scene.defaultCamera.height, 100)),
    orientation: {
      heading: scene.defaultCamera.heading,
      pitch: scene.defaultCamera.pitch,
      roll: 0,
    },
    duration,
  })
}

/** `Rectangle` in radians to the degrees `cameraRig` works in. */
function extentOf(rectangle: Rectangle): Extent {
  return {
    west: CesiumMath.toDegrees(rectangle.west),
    south: CesiumMath.toDegrees(rectangle.south),
    east: CesiumMath.toDegrees(rectangle.east),
    north: CesiumMath.toDegrees(rectangle.north),
  }
}

/**
 * Clip the globe to the study-area bbox — the floating sandbox slab.
 *
 * Four vertical planes, one per bbox edge, with **outward** normals (pointing
 * away from the area). `unionClippingRegions: true` means a point on the
 * positive side of any plane is clipped, so everything outside the box is
 * removed and the inside stays — the slab you see, with the terrain's own cut
 * face as the slab edge.
 *
 * The surrounding globe is gone (dark void), which is the point: the slab
 * floats. The floor of the slab is the base imagery, kept coloured by the
 * view mode. For a ~2 km study area the curvature is negligible, so flat
 * planes at the edge midpoints are within a metre of the true geodesic edge.
 */
function clipGlobeToBBox(viewer: Viewer, bbox: { type: 'Polygon'; coordinates: number[][][] } | null): void {
  if (!isLive(viewer) || !bbox) return
  const ring = bbox.coordinates[0]
  if (!ring || ring.length < 4) return
  // The ring is [[w,s],[e,s],[e,n],[w,n],[w,s]].
  const w = ring[0][0]
  const s = ring[0][1]
  const e = ring[2][0]
  const n = ring[2][1]
  const midLon = (w + e) / 2
  const midLat = (s + n) / 2

  const planes: ClippingPlane[] = []
  const add = (lon: number, lat: number, dir: 'east' | 'west' | 'north' | 'south') => {
    const origin = Cartesian3.fromDegrees(lon, lat)
    const frame = Transforms.eastNorthUpToFixedFrame(origin)
    const east4 = Matrix4.getColumn(frame, 0, new Cartesian4())
    const north4 = Matrix4.getColumn(frame, 1, new Cartesian4())
    const east = new Cartesian3(east4.x, east4.y, east4.z)
    const north = new Cartesian3(north4.x, north4.y, north4.z)
    let normal: Cartesian3
    if (dir === 'east') normal = east
    else if (dir === 'west') normal = Cartesian3.negate(east, new Cartesian3())
    else if (dir === 'north') normal = north
    else normal = Cartesian3.negate(north, new Cartesian3())
    const distance = -Cartesian3.dot(normal, origin)
    planes.push(new ClippingPlane(normal, distance))
  }
  // Normals point INWARD. This is read off Cesium's own shader, not guessed:
  // `clippingFunctionUnion` discards a fragment when
  // `dot(normal, position) + distance <= 0`, so the half-space the normal
  // points *towards* is the one that survives. Outward normals -- the
  // intuitive reading -- put every point behind at least one plane, so the
  // entire globe vanished, including the map floor under the slab. That was
  // the black void the slab used to sit in.
  add(w, midLat, 'east') // west edge, inward = east
  add(e, midLat, 'west') // east edge, inward = west
  add(midLon, s, 'north') // south edge, inward = north
  add(midLon, n, 'south') // north edge, inward = south

  const globe = viewer.scene.globe
  const previous = globe.clippingPlanes
  // Deferred for the same reason `retireLayer` is: destroying the previous
  // collection while the frame that sampled it is still in flight killed the
  // render loop with "This object was destroyed".
  if (previous && !previous.isDestroyed()) {
    setTimeout(() => {
      try {
        previous.destroy()
      } catch {
        // Viewer torn down in the meantime.
      }
    }, 2000)
  }

  globe.clippingPlanes = new ClippingPlaneCollection({
    planes,
    unionClippingRegions: true,
    enabled: true,
    edgeWidth: 1.5,
    edgeColor: Color.fromCssColorString('#0a1410'),
  })
  viewer.scene.requestRender()
}

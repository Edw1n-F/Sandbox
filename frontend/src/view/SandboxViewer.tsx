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
import { Cartesian3, Color, ConstantProperty, Viewer, type ImageryLayer } from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

import { useSceneStore } from '@/state/sceneStore'
import {
  createBaseImageryLayer,
  createOverlayLayer,
  createTerrainProvider,
  polygonToRectangle,
} from './terrainLoader'

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

/** Overlay load status, keyed by layer. Consumed by `LayerToggle`. */
export const useOverlayStatus = create<Record<'wetGrid' | 'heatmap', string>>(() => ({
  wetGrid: 'idle',
  heatmap: 'idle',
}))

/** Camera actions for `CameraController`, without prop drilling. */
export const cameraControl: {
  flyToBbox: () => void
  reset: () => void
} = {
  flyToBbox: () => undefined,
  reset: () => undefined,
}

export default function SandboxViewer() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const baseRef = useRef<ImageryLayer | null>(null)
  const overlaysRef = useRef<Record<'wetGrid' | 'heatmap', ImageryLayer | null>>({
    wetGrid: null,
    heatmap: null,
  })
  const boundaryRef = useRef<string | null>(null)

  const bbox = useSceneStore((s) => s.scene?.bbox ?? null)
  const terrainMeta = useSceneStore((s) => s.terrain)
  const layers = useSceneStore((s) => s.layers)

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

    cameraControl.flyToBbox = () => flyTo(viewer, true)
    cameraControl.reset = () => flyTo(viewer, false)

    return () => {
      overlaysRef.current = { wetGrid: null, heatmap: null }
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
      // Disclose the fallback rather than hiding it; ScenePanel renders this.
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
        flyTo(viewer, true, 0)
        viewer.scene.requestRender()
      }
    })

    const base = createBaseImageryLayer(terrainMeta)
    viewer.imageryLayers.add(base)
    baseRef.current = base

    return () => {
      cancelled = true
      // Guarded: under StrictMode this cleanup can run after destroy().
      if (isLive(viewer) && viewer.imageryLayers.contains(base)) {
        viewer.imageryLayers.remove(base, false)
      }
      baseRef.current = null
    }
  }, [terrainMeta])

  // ---- boundary outline --------------------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!isLive(viewer)) return
    const rectangle = polygonToRectangle(bbox)

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
      if (existing) existing.show = layers.boundary
      return
    }
    const id = viewer.entities.add({
      name: 'Study area boundary',
      rectangle: {
        coordinates: rectangle,
        // RectangleGraphics members are Property objects, not raw booleans.
        fill: new ConstantProperty(false),
        outline: new ConstantProperty(true),
        outlineColor: new ConstantProperty(
          Color.fromCssColorString('#ffd54f').withAlpha(0.95),
        ),
        outlineWidth: new ConstantProperty(2),
      },
      show: layers.boundary,
    })
    boundaryRef.current = id.id
  }, [bbox, layers.boundary])

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

    for (const [key, url, alpha] of wanted) {
      const existing = overlaysRef.current[key]
      const shouldShow = layers[key] && Boolean(url)
      if (existing) {
        // Toggling visibility is cheap; rebuilding the provider re-downloads.
        existing.show = shouldShow
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
          overlaysRef.current[key] = layer
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
    flyTo(viewer, true, 0)
    // Placement happens once per scene load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bbox])

  return <div ref={containerRef} className="absolute inset-0 z-0" />
}

/** Fly to the bbox extent, or fall back to the scene's `defaultCamera`. */
function flyTo(viewer: Viewer, useBbox: boolean, duration = 1.2): void {
  if (!isLive(viewer)) return
  const store = useSceneStore.getState()
  const scene = store.scene
  if (!scene) return

  const rectangle = useBbox ? polygonToRectangle(scene.bbox) : null
  if (rectangle) {
    viewer.camera.flyTo({ destination: rectangle, duration })
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

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
import { Cartesian3, Color, ConstantProperty, Viewer, type ImageryLayer } from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

import { useSceneStore } from '@/state/sceneStore'
import {
  createBaseImageryLayer,
  createOverlayLayer,
  createTerrainProvider,
  polygonToRectangle,
} from './terrainLoader'

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
    if (!viewer || !terrainMeta) return

    const binding = createTerrainProvider(terrainMeta)
    viewer.terrainProvider = binding.terrainProvider

    // Disclose the fallback rather than hiding it; ScenePanel renders this.
    useSceneStore.setState((s) => ({
      scene: s.scene
        ? { ...s.scene, terrainIsReal: binding.terrainIsReal, terrainNote: binding.terrainNote }
        : s.scene,
    }))

    const base = createBaseImageryLayer(terrainMeta)
    viewer.imageryLayers.add(base)
    baseRef.current = base

    return () => {
      if (viewer.imageryLayers.contains(base)) viewer.imageryLayers.remove(base, false)
      baseRef.current = null
    }
  }, [terrainMeta])

  // ---- boundary outline --------------------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
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
    if (!viewer || !terrainMeta) return
    const rectangle = polygonToRectangle(terrainMeta.bbox)
    if (!rectangle) return

    const wanted: Array<['wetGrid' | 'heatmap', string | null | undefined, number]> = [
      ['wetGrid', terrainMeta.wetGridUrl, 0.75],
      ['heatmap', terrainMeta.heatmapUrl, 0.6],
    ]

    for (const [key, url, alpha] of wanted) {
      const existing = overlaysRef.current[key]
      const shouldShow = layers[key] && Boolean(url)
      if (existing) {
        // Toggling visibility is cheap; rebuilding the provider re-downloads.
        existing.show = shouldShow
        continue
      }
      if (!shouldShow) continue
      const layer = createOverlayLayer(url, rectangle, alpha)
      if (layer) {
        viewer.imageryLayers.add(layer)
        overlaysRef.current[key] = layer
      }
    }
  }, [layers, terrainMeta])

  // ---- initial camera placement -----------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    flyTo(viewer, true, 0)
    // Placement happens once per scene load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bbox])

  return <div ref={containerRef} className="absolute inset-0 z-0" />
}

/** Fly to the bbox extent, or fall back to the scene's `defaultCamera`. */
function flyTo(viewer: Viewer, useBbox: boolean, duration = 1.2): void {
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

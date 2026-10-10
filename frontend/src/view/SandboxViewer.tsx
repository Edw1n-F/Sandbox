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
  Cartesian3,
  Color,
  ColorMaterialProperty,
  ConstantProperty,
  GeoJsonDataSource,
  ImageryLayer,
  JulianDate,
  Math as CesiumMath,
  PerspectiveFrustum,
  UrlTemplateImageryProvider,
  Viewer,
} from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import { addOnlineProps, type OnlinePropHandle } from './onlineProps'
import { add3DTileset, type TilesetHandle } from './tilesetLayer'
import { addInterventions } from './interventionLayer'
import {
  createBaseImageryLayer,
  createOverlayLayer,
  createTerrainProvider,
  polygonToRectangle,
} from './terrainLoader'

let propsHandle: OnlinePropHandle | null = null
let tilesetHandle: TilesetHandle | null = null

const useProps = create<{ rev: number }>(() => ({ rev: 0 }))

export function usePropsRevision(): number {
  return useProps((s) => s.rev)
}

export function onlinePropStatus(): { loaded: number; failed: number } {
  return { loaded: propsHandle?.loaded().length ?? 0, failed: propsHandle?.failed().length ?? 0 }
}

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
  const osmBaseRef = useRef<ImageryLayer | null>(null)
  const baseRef = useRef<ImageryLayer | null>(null)
  const overlaysRef = useRef<Record<'wetGrid' | 'heatmap', ImageryLayer | null>>({
    wetGrid: null,
    heatmap: null,
  })
  const boundaryRef = useRef<string | null>(null)
  const waterbodySourceRef = useRef<GeoJsonDataSource | null>(null)
  const infraSourceRef = useRef<GeoJsonDataSource | null>(null)

  const bbox = useSceneStore((s) => s.scene?.bbox ?? null)
  const terrainMeta = useSceneStore((s) => s.terrain)
  const interventions = usePlanStore((s) => s.interventions)
  const showInterventions = useSceneStore((s) => s.layers.interventions)
  const showRealBuildings = useSceneStore((s) => s.layers.realBuildings ?? true)
  const showProps = useSceneStore((s) => s.layers.props ?? true)
  const showWaterbody = useSceneStore((s) => s.layers.waterbody ?? true)
  const showInfrastructure = useSceneStore((s) => s.layers.infrastructure ?? true)
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
    // Required for CLAMP_TO_GROUND / RELATIVE_TO_GROUND to work correctly against
    // the terrain-RGB surface. Without this buildings appear to float or sink.
    viewer.scene.globe.depthTestAgainstTerrain = true

    // This is a local planning sandbox, not a global globe viewer. Disable the
    // space backdrop and keep a neutral background so the study area reads as
    // a design model rather than a planet.
    if (viewer.scene.skyBox) viewer.scene.skyBox.show = false
    if (viewer.scene.sun) viewer.scene.sun.show = false
    if (viewer.scene.moon) viewer.scene.moon.show = false
    viewer.scene.backgroundColor = Color.fromCssColorString('#e8e8e8')
    viewer.scene.screenSpaceCameraController.minimumZoomDistance = 50
    viewer.scene.screenSpaceCameraController.maximumZoomDistance = 20000

    // Narrow the default FOV so a small study area does not look like it is
    // wrapped around a sphere. Cesium manages aspectRatio automatically on
    // the default frustum; replacing it breaks that, so we only adjust fov.
    const frustum = viewer.scene.camera.frustum
    if (frustum instanceof PerspectiveFrustum) {
      frustum.fov = CesiumMath.toRadians(45)
    }

    viewerRef.current = viewer

    // OSM as the permanent base layer — added once here, never removed.
    // This ensures the globe has a full-coverage background so that
    // SingleTileImageryProvider (the Lands Dept texture) does not stretch its
    // edge pixels beyond its imageryBbox rectangle.
    const osm = new ImageryLayer(
      new UrlTemplateImageryProvider({
        url: '/osm-tiles/{z}/{x}/{y}.png',
        maximumLevel: 19,
      })
    )
    viewer.imageryLayers.add(osm)
    osmBaseRef.current = osm

    // Snap the camera to HSK/HT before the scene API responds.
    // setView() is synchronous and unconditional — the user never sees the
    // default full-globe view, not even for a single frame.
    const initScene = useSceneStore.getState().scene
    const initCam = initScene?.defaultCamera
    viewer.camera.setView({
      destination: Cartesian3.fromDegrees(
        initCam?.longitude ?? 113.9935,
        initCam?.latitude  ?? 22.435,
        initCam?.height    ?? 800,
      ),
      orientation: {
        heading: CesiumMath.toRadians(initCam?.heading ?? 0),
        pitch:   CesiumMath.toRadians(initCam?.pitch   ?? -22),
        roll: 0,
      },
    })

    cameraControl.flyToBbox = () => flyTo(viewer, true)
    cameraControl.reset = () => flyTo(viewer, false)

    // If the scene was already loaded before this viewer instance was created
    // (common in StrictMode remounts), frame it with the full bbox fly-in.
    if (initScene) {
      flyTo(viewer, true, 0)
    }

    return () => {
      overlaysRef.current = { wetGrid: null, heatmap: null }
      osmBaseRef.current = null
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
      if (cancelled || !isLive(viewer)) return
      viewer.terrainProvider = binding.terrainProvider
      useSceneStore.setState((s) => ({
        scene: s.scene
          ? {
              ...s.scene,
              terrainIsReal: binding.terrainIsReal,
              terrainNote: binding.terrainNote,
            }
          : s.scene,
      }))
      // Clamp the camera centre to the scene bbox so the user cannot pan into
      // the featureless globe where clamp-to-edge artifacts are visible.
      const ring = bbox?.coordinates?.[0]
      if (ring) {
        const lons = ring.map((p: number[]) => p[0])
        const lats = ring.map((p: number[]) => p[1])
        const margin = 0.25  // degrees of extra room beyond the bbox edge
        const lonMin = Math.min(...lons) - margin
        const lonMax = Math.max(...lons) + margin
        const latMin = Math.min(...lats) - margin
        const latMax = Math.max(...lats) + margin
        viewer.scene.preUpdate.addEventListener(() => {
          if (!isLive(viewer)) return
          const carto = viewer.camera.positionCartographic
          const lon = CesiumMath.toDegrees(carto.longitude)
          const lat = CesiumMath.toDegrees(carto.latitude)
          if (lon < lonMin || lon > lonMax || lat < latMin || lat > latMax) {
            viewer.camera.flyTo({
              destination: Cartesian3.fromDegrees(
                Math.min(Math.max(lon, lonMin), lonMax),
                Math.min(Math.max(lat, latMin), latMax),
                carto.height,
              ),
              duration: 0.3,
            })
          }
        })
      }
      if (binding.terrainIsReal) {
        flyTo(viewer, true, 0)
        viewer.scene.requestRender()
      }
    })

    // createBaseImageryLayer is async (SingleTileImageryProvider.fromUrl must
    // await image load) and may return null when no imagery is configured.
    
    void createBaseImageryLayer(terrainMeta).then((base) => {
      if (!base || cancelled || !isLive(viewer)) return
      viewer.imageryLayers.add(base)
      baseRef.current = base
      viewer.scene.requestRender()
    })
    

    return () => {
      cancelled = true
      const base = baseRef.current
      // Guarded: under StrictMode this cleanup can run after destroy().
      if (base && isLive(viewer) && viewer.imageryLayers.contains(base)) {
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
    if (!isLive(viewer) || !bbox) return
    // setView on first bbox arrival (no animation): the camera is already near
    // the study area from the init setView above, so no fly-in is needed.
    flyTo(viewer, true, 0)
    // Placement happens once per scene load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bbox])

  // ---- 3D Tiles real buildings (6-NW-12D textured meshes) ----------------
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.cityModel3DUrl ?? terrainMeta?.cityModel3dUrl
    if (!isLive(viewer) || !url) {
      tilesetHandle = null
      return
    }

    let cancelled = false
    void add3DTileset(viewer, url).then((handle) => {
      if (cancelled || !handle) return
      tilesetHandle = handle
      handle.tileset.show = showRealBuildings
      viewer.scene.requestRender()
    }).catch((err) => {
      console.warn('[3dtiles] effect error:', err)
    })

    return () => {
      cancelled = true
      tilesetHandle?.dispose()
      tilesetHandle = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terrainMeta?.cityModel3DUrl, terrainMeta?.cityModel3dUrl])

  useEffect(() => {
    if (!tilesetHandle) return
    tilesetHandle.tileset.show = showRealBuildings
    viewerRef.current?.scene.requestRender()
  }, [showRealBuildings])

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

  // ---- waterbody layer (6-NW-12D) ----------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.waterbodyUrl
    if (!isLive(viewer) || !url) return

    let cancelled = false
    void GeoJsonDataSource.load(url).then((source) => {
      if (cancelled || !isLive(viewer)) return
      source.entities.values.forEach((e) => {
        if (e.polygon) {
          e.polygon.material = new ColorMaterialProperty(
            Color.fromCssColorString('#1565C0').withAlpha(0.55),
          )
          e.polygon.outline = new ConstantProperty(false)
        }
      })
      source.show = showWaterbody
      void viewer.dataSources.add(source)
      waterbodySourceRef.current = source
      viewer.scene.requestRender()
    })

    return () => {
      cancelled = true
      const src = waterbodySourceRef.current
      if (src && isLive(viewer) && viewer.dataSources.contains(src)) {
        viewer.dataSources.remove(src, true)
      }
      waterbodySourceRef.current = null
    }
  }, [terrainMeta])

  useEffect(() => {
    const src = waterbodySourceRef.current
    if (!src || !isLive(viewerRef.current)) return
    src.show = showWaterbody
    viewerRef.current.scene.requestRender()
  }, [showWaterbody])

  // ---- infrastructure layer (6-NW-12D) ------------------------------------
  useEffect(() => {
    const viewer = viewerRef.current
    const url = terrainMeta?.infrastructureUrl
    if (!isLive(viewer) || !url) return

    let cancelled = false
    void GeoJsonDataSource.load(url).then((source) => {
      if (cancelled || !isLive(viewer)) return
      source.entities.values.forEach((e) => {
        if (e.polygon) {
          // height_m is stored as a ConstantProperty in the PropertyBag
          const heightProp = e.properties?.getValue(JulianDate.now())
          const h: number = (heightProp as Record<string, number> | null)?.height_m ?? 0
          e.polygon.material = new ColorMaterialProperty(
            Color.fromCssColorString('#FF8F00').withAlpha(0.65),
          )
          e.polygon.outline = new ConstantProperty(true)
          e.polygon.outlineColor = new ConstantProperty(
            Color.fromCssColorString('#E65100').withAlpha(0.9),
          )
          if (h > 0) {
            e.polygon.height = new ConstantProperty(0)
            e.polygon.extrudedHeight = new ConstantProperty(h)
          }
        }
      })
      source.show = showInfrastructure
      void viewer.dataSources.add(source)
      infraSourceRef.current = source
      viewer.scene.requestRender()
    })

    return () => {
      cancelled = true
      const src = infraSourceRef.current
      if (src && isLive(viewer) && viewer.dataSources.contains(src)) {
        viewer.dataSources.remove(src, true)
      }
      infraSourceRef.current = null
    }
  }, [terrainMeta])

  useEffect(() => {
    const src = infraSourceRef.current
    if (!src || !isLive(viewerRef.current)) return
    src.show = showInfrastructure
    viewerRef.current.scene.requestRender()
  }, [showInfrastructure])
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

    let disposed = false
    let dispose: (() => void) | undefined

    void addInterventions(viewer, {
      type: 'FeatureCollection',
      features: showInterventions ? interventions.features : [],
    }).then((fn) => {
      // The effect may have been torn down while the GeoJSON was in flight.
      if (disposed) {
        fn()
        return
      }
      dispose = fn
    })

    return () => {
      disposed = true
      dispose?.()
    }
  }, [showInterventions, interventions])

  return <div ref={containerRef} className="absolute inset-0 z-0" />
}

/** Fly to the bbox extent, or fall back to the scene's `defaultCamera`. */
function flyTo(viewer: Viewer, useBbox: boolean, duration = 1.2): void {
  if (!isLive(viewer)) return
  const store = useSceneStore.getState()
  const scene = store.scene
  if (!scene) return

  const rectangle = useBbox ? polygonToRectangle(scene.bbox) : null
  const defaultCamera = scene.defaultCamera

  // Oblique perspective view: heading north, pitched down 35°. This gives a
  // conventional architectural perspective rather than the top-down view that
  // Cesium uses when the destination is a Rectangle.
  const orientation = {
    heading: CesiumMath.toRadians(defaultCamera.heading ?? 0),
    pitch: CesiumMath.toRadians(defaultCamera.pitch ?? -35),
    roll: 0,
  }

  if (rectangle) {
    const west = CesiumMath.toDegrees(rectangle.west)
    const east = CesiumMath.toDegrees(rectangle.east)
    const south = CesiumMath.toDegrees(rectangle.south)
    const north = CesiumMath.toDegrees(rectangle.north)
    const lon = (west + east) / 2
    const lat = (south + north) / 2

    // Compute a camera height that frames the bbox with a comfortable margin.
    // The diagonal in metres is approximated locally at the latitude of the area.
    const latRad = CesiumMath.toRadians(lat)
    const cosLat = Math.cos(latRad)
    const dx = (east - west) * 111320 * cosLat
    const dy = (north - south) * 110540
    const diagonal = Math.sqrt(dx * dx + dy * dy)
    const height = Math.max(defaultCamera.height ?? diagonal * 1.2, 500)

    viewer.camera.flyTo({
      destination: Cartesian3.fromDegrees(lon, lat, height),
      orientation,
      duration,
    })
    return
  }

  const lon = defaultCamera.longitude ?? scene.center.coordinates[0]
  const lat = defaultCamera.latitude ?? scene.center.coordinates[1]
  viewer.camera.flyTo({
    destination: Cartesian3.fromDegrees(lon, lat, Math.max(defaultCamera.height, 100)),
    orientation,
    duration,
  })
}

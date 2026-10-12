/**
 * The 3D city model: massing envelopes, extruded, and **directly editable**.
 *
 * ## Where the geometry comes from
 *
 * OSM building footprints were the first choice and are unreachable from this
 * host -- Overpass 504s or returns empty across five mirrors, and Microsoft
 * GlobalML now returns 409. So `make_citymodel.py` generates massing envelopes
 * from the ESA WorldCover `paved` class: the land-use input is real, the
 * individual footprints are not surveyed, and `citymodel_meta.json` says so.
 * The viewer shows that status rather than presenting a skyline as fact.
 *
 * ## Why extruded polygons rather than a glTF model
 *
 * A glTF massing model would be one draw call, but it would be opaque: you
 * cannot change a building's height in it, and changing the model is the point.
 * Cesium entities are per-feature `Property` bags, so a height edit is a
 * property assignment with no rebuild, no re-upload and no geometry regeneration.
 * That is the whole argument for geometry over a mesh here.
 *
 * ## Editing
 *
 * `setBuildingHeight` rewrites one envelope's `extrudedHeight` in place.
 * `setAllHeights` applies a multiplier across the model, which is the useful
 * gesture for a scenario -- "what if the district were twice as tall" -- and is
 * the same operation the AI module performs when a prompt asks for it.
 *
 * ## What `applyMassingOps` is for
 *
 * This is the AI's path into the model, and it is deliberately the *only* one:
 * §3.3.2 requires AI-proposed and hand-drawn features to share a pipeline, so
 * the AI emits the same op shape the panel's buttons produce and everything
 * lands here.
 *
 * Two properties matter more than the operations themselves:
 *
 * - **Scope.** Every op carries a polygon resolved by the backend, and a
 *   building is in scope when its centroid falls inside it. "Make the western
 *   blocks 30% taller" therefore reaches a few hundred buildings rather than
 *   all 2,639 -- which is the difference between answering the question and
 *   returning a different plausible-looking answer to it.
 * - **Idempotence.** Heights, orientations and placements all reset to the
 *   generated state before the ops re-apply, so the model depends only on which
 *   plan is selected and never on how many times React has re-run the effect.
 */
import {
  Cartesian3,
  Color,
  ConstantProperty,
  Entity,
  GeoJsonDataSource,
  HeightReference,
  PolygonHierarchy,
  ScreenSpaceEventHandler,
  type MaterialProperty,
  type Viewer,
} from 'cesium'

import { sampleDemHeight, whenDemReady } from './demProvider'
import {
  createBuildingMaterial,
  setModelLook,
  TECH_STATES,
  type BuildingMaterial,
  type BuildingState,
  type MaterialLook,
} from './techMaterial'

export interface BuildingProperties {
  id: string | null
  building_type?: string
  height_m?: number
  storeys?: number
  height_class?: string
  height_source?: string
  footprint_source?: string
  provisional?: boolean
}

export interface MassingFeature {
  type: 'Feature'
  geometry: { type: string; coordinates: number[][][] }
  properties: BuildingProperties
}

export interface MassingCollection {
  type: 'FeatureCollection'
  features: MassingFeature[]
}

export interface MassingScope {
  kind?: 'all' | 'region' | 'site'
  region?: string | null
  siteId?: string | null
  label?: string
  /**
   * The resolved polygon, computed **by the backend** from a region name or an
   * opaque site id. The model never sees or supplies this, so a scoped edit
   * cannot rest on a coordinate the model wrote.
   */
  ring?: number[][] | null
}

export interface MassingPlacement {
  id: string
  ring: number[][]
  heightM: number
}

export interface MassingOp {
  op: 'scale_all' | 'set_site' | 'rotate' | 'place' | 'move' | 'reset' | 'resize'
  multiplier?: number
  heightM?: number | null
  /** A change in metres rather than an absolute height. */
  deltaM?: number | null
  degrees?: number | null
  /** Translation in metres, east- and north-positive. */
  dxM?: number | null
  dyM?: number | null
  scope?: MassingScope | null
  placements?: MassingPlacement[]
  /** Direct target coordinate for move (inside the study area). */
  targetLon?: number | null
  targetLat?: number | null
  /** Footprint size scale for resize. 1.2 = 20% wider. */
  widthScale?: number | null
  depthScale?: number | null
}

export interface MassingOpResult {
  /** Buildings the op actually reached -- the honest answer to "did it work?". */
  changed: number
  /** Buildings in the model, including any this plan added. */
  total: number
  /** Buildings this plan added, as opposed to editing. */
  placed: number
  /** Human-readable scope, e.g. "the west". */
  scope: string
  op: MassingOp['op']
}

export interface CityModelHandle {
  /** Every building entity, keyed by its `properties.id`. */
  entities: Map<string, Entity>
  /** Currently selected building id, or null. */
  selected: () => string | null
  select: (id: string | null) => void
  setBuildingHeight: (id: string, metres: number) => void
  /** Scale every building's height. Returns the number actually changed. */
  setAllHeights: (multiplier: number) => number
  /**
   * Switch between the lit survey surface and the emissive tactical one.
   *
   * Uniform writes only — no primitive is rebuilt — which is why a mode switch
   * is instant with 2,639 envelopes on screen. See `techMaterial.ts`.
   */
  setLook: (look: MaterialLook) => void
  /**
   * Re-read the ground under every envelope from the DEM.
   *
   * The model and the terrain image load in parallel and the model usually wins,
   * so on first paint the envelopes are standing on sea level. Returns the number
   * whose base actually moved, so a caller can say so rather than assume it.
   */
  syncGroundHeights: () => number
  /** Envelopes standing on a DEM sample, out of the total. */
  groundedCount: () => number
  /**
   * Apply massing operations from the active plan. **Idempotent.**
   *
   * Every building returns to its generated height *and orientation* first, and
   * any block a previous application placed is removed, then the ops apply in
   * order. Without that reset a second call would compound: `setAllHeights(1.25)`
   * twice yields x1.5625, and since this runs from a `useEffect` keyed on the
   * plan, any re-render would quietly inflate the skyline. The generated heights
   * are captured at load precisely so this is possible.
   */
  applyMassingOps: (ops: MassingOp[]) => MassingOpResult[]
  meanHeight: () => number
  /** Buildings added by the active plan, as opposed to the generated model. */
  placedCount: () => number
  dispose: () => void
}

const BASE_HEIGHT = 34

/**
 * Survey-mode albedo per state.
 *
 * Only used by the shader's `u_lit` branch. The tactical mode uses the rim
 * colours in `TECH_STATES` instead, because there the body is spoken for by the
 * height ramp.
 */
const COLOURS = {
  base: Color.fromCssColorString('#b9c2cf'),
  selected: Color.fromCssColorString('#ffd54f'),
  edited: Color.fromCssColorString('#ff8f3f'),
  placed: Color.fromCssColorString('#7ee3d0'),
}

/**
 * Parsed once. `styleFor` runs over every envelope on every repaint, and
 * `fromCssColorString` allocates.
 */
const TECH_COLOURS: Record<BuildingState, Color> = {
  base: Color.fromCssColorString(TECH_STATES.base.edge),
  selected: Color.fromCssColorString(TECH_STATES.selected.edge),
  edited: Color.fromCssColorString(TECH_STATES.edited.edge),
  placed: Color.fromCssColorString(TECH_STATES.placed.edge),
}

// Metres per degree at the study area's latitude. Rotation is done in metres
// because degrees are not a unit of length: rotating in degree space skews by
// cos(lat), which at 22.4 deg N is a 7% shear on a north-south edge.
const M_PER_DEG_LAT = 110_574

const mPerDegLon = (lat: number) => 111_320 * Math.cos((lat * Math.PI) / 180)

const baseHeightOf = (f: MassingFeature) =>
  typeof f.properties?.height_m === 'number' ? f.properties.height_m : BASE_HEIGHT

/**
 * Ring centroid. Every building is axis-aligned and 5-point, so this is exact.
 *
 * Exported for tests, along with `pointInRing`, `rotateRing` and `translateRing`.
 * These four decide *which buildings a plan touches and where they end up*, and they
 * are pure, so there is no reason they cannot be covered directly. `pointInRing` in
 * particular has to agree with the backend's `_point_in_ring` to the metre; when it
 * drifts, a scoped op silently applies to the whole district or to nothing.
 */
export function ringCentroid(ring: number[][]): [number, number] {
  const pts = ring.slice(0, Math.max(ring.length - 1, 1))
  if (!pts.length) return [0, 0]
  const sum = pts.reduce((acc, p) => [acc[0] + p[0], acc[1] + p[1]], [0, 0])
  return [sum[0] / pts.length, sum[1] / pts.length]
}

/** Ray casting, matching the backend's `_point_in_ring`. Exported for tests. */
export function pointInRing(lon: number, lat: number, ring: number[][]): boolean {
  let inside = false
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [x1, y1] = ring[i]
    const [x2, y2] = ring[i + 1]
    if (y1 > lat !== y2 > lat) {
      const crossing = x1 + ((lat - y1) * (x2 - x1)) / (y2 - y1)
      if (lon < crossing) inside = !inside
    }
  }
  return inside
}

/** Rotate a ring clockwise about its own centroid, working in metres. Exported for tests. */
export function rotateRing(ring: number[][], degrees: number): number[][] {
  const [cx, cy] = ringCentroid(ring)
  const kx = mPerDegLon(cy)
  const ky = M_PER_DEG_LAT
  const theta = (degrees * Math.PI) / 180
  const cos = Math.cos(theta)
  const sin = Math.sin(theta)
  return ring.map(([lon, lat]) => {
    const dx = (lon - cx) * kx
    const dy = (lat - cy) * ky
    return [
      Number((cx + (dx * cos - dy * sin) / kx).toFixed(7)),
      Number((cy + (dx * sin + dy * cos) / ky).toFixed(7)),
    ]
  })
}

/** Translate a ring by metres, east- and north-positive. */
/** Translate a ring in metres, east- and north-positive. Exported for tests. */
export function translateRing(ring: number[][], dxM: number, dyM: number): number[][] {
  const [, cy] = ringCentroid(ring)
  const kx = mPerDegLon(cy)
  const ky = M_PER_DEG_LAT
  return ring.map(([lon, lat]) => [
    Number((lon + dxM / kx).toFixed(7)),
    Number((lat + dyM / ky).toFixed(7)),
  ])
}

/**
 * Scale a ring's width and/or depth about its own centroid.
 *
 * Works in metres (not degrees) so the scaling is isotropic in plan regardless
 * of latitude. `ws` scales the east-west axis, `ds` the north-south axis; each
 * defaults to 1.0. Exported for tests.
 */
export function scaleRing(ring: number[][], ws: number, ds: number): number[][] {
  const [cx, cy] = ringCentroid(ring)
  const kx = mPerDegLon(cy)
  const ky = M_PER_DEG_LAT
  return ring.map(([lon, lat]) => {
    const dx = (lon - cx) * kx * ws
    const dy = (lat - cy) * ky * ds
    return [
      Number((cx + dx / kx).toFixed(7)),
      Number((cy + dy / ky).toFixed(7)),
    ]
  })
}

/**
 * Rewrite a polygon entity's footprint.
 *
 * `hierarchy` is a Property, so this is a live geometry swap with no rebuild --
 * the same argument as the height edit, applied to the plan view. (It is
 * `hierarchy`, not `positions`: the latter was removed from `PolygonGraphics`
 * some time before this Cesium version.)
 */
function setRing(entity: Entity, ring: number[][]): void {
  if (!entity.polygon || ring.length < 4) return
  entity.polygon.hierarchy = new ConstantProperty(
    new PolygonHierarchy(ring.map(([lon, lat]) => Cartesian3.fromDegrees(lon, lat))),
  )
}

export async function addCityModel(
  viewer: Viewer,
  collection: MassingCollection,
  onSelect?: (id: string | null) => void,
): Promise<CityModelHandle | null> {
  if (!collection.features.length) return null
  if (viewer.isDestroyed()) return null

  const source = new GeoJsonDataSource('citymodel')
  await source.load(collection as unknown as object)

  const entities = new Map<string, Entity>()
  // Reverse lookup, and the reason it exists: `GeoJsonDataSource` sets
  // `entity.name` only from a `title` or `name` property, and these features have
  // neither -- their key is `properties.id`, which Cesium does *not* copy onto
  // the entity. So `picked.id.name` is undefined, and a click resolved to no id at
  // all. The panel's "click a building to select it" therefore did nothing, for
  // every building, silently: 915 clicks across the district selected none. A
  // reverse map keyed on entity identity cannot drift from the entity set.
  const idByEntity = new Map<Entity, string>()
  // The height the generator gave this building. Every op is applied relative to
  // this, never to the current height, which is what makes apply idempotent.
  const baseHeights = new Map<string, number>()
  // The generated footprint, for the same reason: rotation is cumulative unless
  // it is applied to the original ring every time.
  const baseRings = new Map<string, number[][]>()
  const centroids = new Map<string, [number, number]>()
  // Ground level under each envelope, from the DEM. See `sampleDemHeight` for
  // why this is read from the raster rather than clamped to the globe.
  const ground = new Map<string, number>()
  // The envelope's own height above its ground -- *not* the absolute top. Kept
  // here so `meanHeight` reports what the generator's `heightM` block reports,
  // and so every op has a storey count to scale rather than a ground-dependent
  // absolute to guess at.
  const storeys = new Map<string, number>()
  /** One shader surface per envelope, assigned once and never swapped. */
  const surfaces = new Map<string, BuildingMaterial>()

  // Height range across the whole model, for the tactical ramp. Widened by every
  // block a plan places, so a 200 m tower does not saturate the ramp on arrival.
  let heightMin = Infinity
  let heightMax = -Infinity

  source.entities.values.forEach((entity, i) => {
    const feature = collection.features[i]
    const id = feature.properties?.id ?? `bldg-${i}`
    if (!entity.polygon) return
    const metres = baseHeightOf(feature)
    const ring = (feature.geometry?.coordinates?.[0] ?? []).map((p) => [p[0], p[1]])
    baseHeights.set(id, metres)
    storeys.set(id, metres)
    heightMin = Math.min(heightMin, metres)
    heightMax = Math.max(heightMax, metres)
    if (ring.length >= 4) {
      baseRings.set(id, ring)
      const centre = ringCentroid(ring)
      centroids.set(id, centre)
      const elevation = sampleDemHeight(centre[0], centre[1])
      // No sample yet means the DEM image has not finished decoding. 0 is the
      // honest fallback for "unknown" here -- it reproduces the old behaviour
      // rather than inventing a plausible elevation -- and
      // `syncGroundHeights` corrects it the moment the raster lands.
      ground.set(id, elevation ?? 0)
    }
    entity.polygon.height = new ConstantProperty(ground.get(id) ?? 0)
    entity.polygon.extrudedHeight = new ConstantProperty((ground.get(id) ?? 0) + metres)
    entity.polygon.heightReference = new ConstantProperty(HeightReference.NONE)
    entity.polygon.outline = new ConstantProperty(false)
    const surface = createBuildingMaterial(styleFor('base', metres))
    surfaces.set(id, surface)
    // Assigned once, here, and never reassigned: the entity layer rebuilds the
    // primitive whenever this property changes. See `techMaterial.ts`.
    entity.polygon.material = surface.property as unknown as MaterialProperty
    entities.set(id, entity)
    idByEntity.set(entity, id)
  })

  await viewer.dataSources.add(source)
  viewer.scene.requestRender()

  let selectedId: string | null = null
  const edited = new Set<string>()
  /** Buildings added by the active plan, as opposed to the generated model. */
  const placedIds = new Set<string>()

  /** The surface style for a state, at a given height above its own ground. */
  function styleFor(state: BuildingState, metres: number) {
    const span = Math.max(heightMax - heightMin, 1)
    return {
      body: COLOURS[state],
      edge: TECH_COLOURS[state],
      glow: TECH_STATES[state].glow,
      height01: (metres - heightMin) / span,
    }
  }

  /** Which state an envelope is in, most specific first. */
  const stateOf = (id: string): BuildingState => {
    if (selectedId === id) return 'selected'
    if (placedIds.has(id)) return 'placed'
    return edited.has(id) ? 'edited' : 'base'
  }

  /**
   * Restyle one envelope. Uniform writes only.
   *
   * Deliberately *not* `entity.polygon.material = ...`: the entity layer watches
   * that property and rebuilds the primitive when it changes, which with 2,639
   * envelopes turns a click-to-select into a visible stall.
   */
  const paint = (id: string) => {
    surfaces.get(id)?.setStyle(styleFor(stateOf(id), storeys.get(id) ?? BASE_HEIGHT))
  }

  const paintAll = () => {
    for (const id of entities.keys()) paint(id)
  }

  /**
   * Put every envelope back on its ground, if a DEM is available.
   *
   * Returns the number whose base actually moved, so a caller can report it
   * rather than assume it worked.
   */
  function syncGround(): number {
    let moved = 0
    for (const id of entities.keys()) {
      const centre = centroids.get(id)
      const entity = entities.get(id)
      if (!centre || !entity?.polygon) continue
      const elevation = sampleDemHeight(centre[0], centre[1])
      // Still no raster: leave the honest 0 rather than guess.
      if (elevation === null) continue
      if (Math.abs((ground.get(id) ?? 0) - elevation) < 0.01) continue
      ground.set(id, elevation)
      const storey = storeys.get(id) ?? BASE_HEIGHT
      entity.polygon.height = new ConstantProperty(elevation)
      entity.polygon.extrudedHeight = new ConstantProperty(elevation + storey)
      moved += 1
    }
    if (moved) viewer.scene.requestRender()
    return moved
  }

  /**
   * Write an envelope's height, relative to its own ground.
   *
   * Single funnel on purpose: `height` and `extrudedHeight` are two properties
   * that must agree, and an op that set one and forgot the other would leave a
   * building hanging in the air or buried in a hill.
   */
  const setStoreys = (id: string, metres: number): boolean => {
    const entity = entities.get(id)
    if (!entity?.polygon) return false
    const storey = Math.max(1, metres)
    const base = ground.get(id) ?? 0
    entity.polygon.height = new ConstantProperty(base)
    entity.polygon.extrudedHeight = new ConstantProperty(base + storey)
    storeys.set(id, storey)
    heightMax = Math.max(heightMax, storey)
    heightMin = Math.min(heightMin, storey)
    return true
  }

  // Stand the envelopes on the terrain once it becomes readable.
  //
  // The model JSON and the DEM image race each other and either can win, so this
  // subscribes rather than sampling once: envelopes created before the raster is up
  // get a sea-level base, and at 141 m of relief that buries every uphill block
  // inside a hill with nothing on screen to say so.
  //
  // Deliberately not awaited. `whenDemReady` only settles once the raster has an
  // extent, which a scene without a DEM never reaches — and a scene without a DEM is
  // a supported configuration, so blocking here would take the city model down over
  // terrain it never needed.
  void whenDemReady().then(() => {
    if (viewer.isDestroyed()) return
    // Ops may already have run against a sea-level base, which is fine: `storeys`
    // holds each envelope's own height, so rebuilding height/extrudedHeight from it
    // preserves whatever the plan asked for.
    if (syncGround()) onSelect?.(selectedId)
  })

  const select = (id: string | null) => {
    if (selectedId) paint(selectedId)
    selectedId = id
    if (id) paint(id)
    onSelect?.(id)
    viewer.scene.requestRender()
  }

  // Click-to-select is deliberately disabled. Per-building selection and
  // height editing was removed from the UI (the prototype is a sandbox you run
  // scenarios on, not a per-building sculptor), and `scene.pick` across 2,639
  // entities on every click was a measurable source of input lag. The handler
  // is still constructed so the rest of the dispose path is unchanged; it just
  // attaches no LEFT_CLICK action.
  const handler = new ScreenSpaceEventHandler(viewer.scene.canvas)
  void handler

  return {
    entities,
    selected: () => selectedId,
    select,
    setBuildingHeight: (id, metres) => {
      if (!setStoreys(id, metres)) return
      edited.add(id)
      paint(id)
      viewer.scene.requestRender()
    },
    setAllHeights: (multiplier) => {
      let changed = 0
      for (const id of entities.keys()) {
        const base = baseHeights.get(id) ?? BASE_HEIGHT
        if (!setStoreys(id, base * multiplier)) continue
        edited.add(id)
        changed += 1
      }
      paintAll()
      viewer.scene.requestRender()
      return changed
    },
    setLook: (look) => {
      setModelLook(surfaces, look)
      viewer.scene.requestRender()
    },
    syncGroundHeights: () => syncGround(),
    groundedCount: () => {
      let n = 0
      for (const id of entities.keys()) if ((ground.get(id) ?? 0) !== 0) n += 1
      return n
    },
    applyMassingOps: (ops) => {
      // ---- reset to the generated state, always ---------------------------- //
      // This is the whole point: the result depends only on the ops, never on how
      // many times this has been called, and never on what the user clicked in
      // between. Removing previously *placed* blocks first is the other half --
      // otherwise switching plans twice would leave a ghost skyline behind.
      for (const id of placedIds) {
        const ghost = entities.get(id)
        if (ghost) {
          idByEntity.delete(ghost)
          source.entities.remove(ghost)
        }
        entities.delete(id)
        baseHeights.delete(id)
        baseRings.delete(id)
        centroids.delete(id)
        ground.delete(id)
        storeys.delete(id)
        surfaces.delete(id)
      }
      placedIds.clear()

      for (const id of entities.keys()) {
        setStoreys(id, baseHeights.get(id) ?? BASE_HEIGHT)
        const ring = baseRings.get(id)
        const entity = entities.get(id)
        if (ring && entity) setRing(entity, ring)
        edited.delete(id)
      }

      const results: MassingOpResult[] = []
      for (const op of ops) {
        const ring = op.scope?.ring ?? null
        const label = op.scope?.label ?? 'the whole study area'
        // An op with no resolved scope affects everything, which is also the
        // correct reading of a plan written before scoping existed.
        const targets = new Set(
          [...entities.keys()].filter((id) => {
            const c = centroids.get(id)
            return !ring || (c ? pointInRing(c[0], c[1], ring) : false)
          }),
        )

        let changed = 0
        if (op.op === 'reset') {
          changed = targets.size
        } else if (op.op === 'scale_all') {
          const m = op.multiplier
          if (typeof m !== 'number' || !Number.isFinite(m) || m <= 0) continue
          targets.forEach((id) => {
            const base = baseHeights.get(id) ?? BASE_HEIGHT
            if (!setStoreys(id, base * m)) return
            edited.add(id)
            changed += 1
          })
        } else if (op.op === 'set_site') {
          // An absolute height, or a change in metres. Both are applied to the
          // *generated* height, so "+30 m" twice is still +30 m.
          const absolute = typeof op.heightM === 'number' ? op.heightM : null
          const delta = typeof op.deltaM === 'number' ? op.deltaM : null
          if (absolute === null && delta === null) continue
          const step = delta as number
          targets.forEach((id) => {
            const base = baseHeights.get(id) ?? BASE_HEIGHT
            const next = absolute !== null ? absolute : base + step
            if (!Number.isFinite(next) || next <= 0) return
            if (!setStoreys(id, Math.min(800, next))) return
            edited.add(id)
            changed += 1
          })
        } else if (op.op === 'rotate') {
          const d = op.degrees
          if (typeof d !== 'number' || !Number.isFinite(d) || d === 0) continue
          targets.forEach((id) => {
            const entity = entities.get(id)
            const ring0 = baseRings.get(id)
            if (!entity?.polygon || !ring0) return
            setRing(entity, rotateRing(ring0, d))
            edited.add(id)
            changed += 1
          })
        } else if (op.op === 'move') {
          // Two modes: metric offset (dx/dy) or direct target coordinate.
          // The coordinate must be inside the study area; one outside is a
          // no-op (the backend refuses it too).
          if (typeof op.targetLon === 'number' && typeof op.targetLat === 'number') {
            targets.forEach((id) => {
              const entity = entities.get(id)
              const ring0 = baseRings.get(id)
              if (!entity?.polygon || !ring0) return
              // Reposition the ring so its centroid lands on the target.
              const [cx, cy] = ringCentroid(ring0)
              const dLon = op.targetLon! - cx
              const dLat = op.targetLat! - cy
              setRing(entity, ring0.map(([lon, lat]) => [lon + dLon, lat + dLat]))
              centroids.set(id, [op.targetLon!, op.targetLat!])
              edited.add(id)
              changed += 1
            })
          } else {
            const dx = typeof op.dxM === 'number' ? op.dxM : 0
            const dy = typeof op.dyM === 'number' ? op.dyM : 0
            if (!Number.isFinite(dx) || !Number.isFinite(dy) || (dx === 0 && dy === 0)) {
              continue
            }
            targets.forEach((id) => {
              const entity = entities.get(id)
              const ring0 = baseRings.get(id)
              if (!entity?.polygon || !ring0) return
              // Always from the *generated* ring, so two moves compose the way two
              // height changes do, rather than the second compounding the first.
              setRing(entity, translateRing(ring0, dx, dy))
              const [lon, lat] = ringCentroid(ring0)
              centroids.set(id, [
                lon + dx / mPerDegLon(lat),
                lat + dy / M_PER_DEG_LAT,
              ])
              edited.add(id)
              changed += 1
            })
          }
        } else if (op.op === 'resize') {
          // Scale the footprint's width and/or depth. The ring is rebuilt from
          // its centroid: each vertex moves radially by the scale factor.
          const ws = typeof op.widthScale === 'number' ? op.widthScale : 1
          const ds = typeof op.depthScale === 'number' ? op.depthScale : 1
          if (!Number.isFinite(ws) || !Number.isFinite(ds) || (ws === 1 && ds === 1)) {
            continue
          }
          targets.forEach((id) => {
            const entity = entities.get(id)
            const ring0 = baseRings.get(id)
            if (!entity?.polygon || !ring0) return
            setRing(entity, scaleRing(ring0, ws, ds))
            edited.add(id)
            changed += 1
          })
        } else if (op.op === 'place') {
          // New blocks, from real geometry the backend generated on land the
          // analysis classifies as built up. They join the same model, so they
          // are selectable, scalable and resettable exactly like generated ones.
          const hidden = entities.values().next().value?.show === false
          for (const block of op.placements ?? []) {
            if (block.ring?.length < 4 || entities.has(block.id)) continue
            const centre = ringCentroid(block.ring)
            // Placed blocks stand on the same ground as everything else, so a
            // tower proposed for a slope does not appear to float over it.
            const elevation = sampleDemHeight(centre[0], centre[1]) ?? 0
            const surface = createBuildingMaterial(styleFor('placed', block.heightM))
            const added = source.entities.add({
              id: block.id,
              name: block.id,
              show: !hidden,
              polygon: {
                hierarchy: new PolygonHierarchy(
                  block.ring.map(([lon, lat]) => Cartesian3.fromDegrees(lon, lat)),
                ),
                material: surface.property as unknown as MaterialProperty,
                height: new ConstantProperty(elevation),
                extrudedHeight: new ConstantProperty(elevation + Math.max(1, block.heightM)),
                heightReference: new ConstantProperty(HeightReference.NONE),
                outline: new ConstantProperty(false),
              },
            })
            entities.set(block.id, added)
            idByEntity.set(added, block.id)
            baseHeights.set(block.id, block.heightM)
            baseRings.set(block.id, block.ring)
            centroids.set(block.id, centre)
            ground.set(block.id, elevation)
            storeys.set(block.id, block.heightM)
            surfaces.set(block.id, surface)
            heightMax = Math.max(heightMax, block.heightM)
            placedIds.add(block.id)
            changed += 1
          }
        } else {
          continue
        }

        results.push({ changed, total: entities.size, placed: placedIds.size,
          scope: label, op: op.op })
      }

      // Restyle everything from the final state rather than incrementally: the
      // height ramp is relative to the model's range, which a `place` op can
      // widen, so an envelope touched early can need a new colour at the end.
      paintAll()
      viewer.scene.requestRender()
      return results
    },
    placedCount: () => placedIds.size,
    meanHeight: () => {
      // The generator's own `heightM` block reports storey counts, so this
      // averages storeys rather than absolute tops. Reading `extrudedHeight`
      // instead would drift upward as the terrain rises under the district and
      // make the panel disagree with `citymodel_meta.json`.
      let sum = 0
      for (const value of storeys.values()) sum += value
      return storeys.size ? sum / storeys.size : 0
    },
    dispose: () => {
      handler.destroy()
      surfaces.clear()
      if (!viewer.isDestroyed() && viewer.dataSources.contains(source)) {
        viewer.dataSources.remove(source, true)
      }
    },
  }
}

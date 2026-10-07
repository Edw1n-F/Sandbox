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
 */
import {
  Cartesian2,
  Color,
  ColorMaterialProperty,
  ConstantProperty,
  Entity,
  GeoJsonDataSource,
  HeightReference,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  type Viewer,
} from 'cesium'

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

export interface CityModelHandle {
  /** Every building entity, keyed by its `properties.id`. */
  entities: Map<string, Entity>
  /** Currently selected building id, or null. */
  selected: () => string | null
  select: (id: string | null) => void
  setBuildingHeight: (id: string, metres: number) => void
  /** Scale every building's height. Returns the number actually changed. */
  setAllHeights: (multiplier: number) => number
  meanHeight: () => number
  dispose: () => void
}

const BASE_HEIGHT = 34
const COLOURS = {
  base: Color.fromCssColorString('#b9c2cf'),
  selected: Color.fromCssColorString('#ffd54f'),
  edited: Color.fromCssColorString('#ff8f3f'),
}

const baseHeightOf = (f: MassingFeature) =>
  typeof f.properties?.height_m === 'number' ? f.properties.height_m : BASE_HEIGHT

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
  source.entities.values.forEach((entity, i) => {
    const feature = collection.features[i]
    const id = feature.properties?.id ?? `bldg-${i}`
    if (!entity.polygon) return
    const metres = baseHeightOf(feature)
    entity.polygon.material = new ColorMaterialProperty(COLOURS.base.withAlpha(0.92))
    entity.polygon.height = new ConstantProperty(0)
    entity.polygon.extrudedHeight = new ConstantProperty(metres)
    entity.polygon.heightReference = new ConstantProperty(HeightReference.NONE)
    entity.polygon.outline = new ConstantProperty(false)
    entities.set(id, entity)
  })

  await viewer.dataSources.add(source)
  viewer.scene.requestRender()

  let selectedId: string | null = null
  const edited = new Set<string>()

  const paint = (entity: Entity, colour: Color) => {
    if (entity.polygon) {
      entity.polygon.material = new ColorMaterialProperty(colour.withAlpha(0.92))
    }
  }

  const select = (id: string | null) => {
    if (selectedId) {
      const prev = entities.get(selectedId)
      if (prev) paint(prev, edited.has(selectedId) ? COLOURS.edited : COLOURS.base)
    }
    selectedId = id
    if (id) {
      const next = entities.get(id)
      if (next) paint(next, COLOURS.selected)
    }
    onSelect?.(id)
    viewer.scene.requestRender()
  }

  // Click-to-select. Left drag is excluded so panning the globe still works.
  const handler = new ScreenSpaceEventHandler(viewer.scene.canvas)
  handler.setInputAction((movement: ScreenSpaceEventHandler.PositionedEvent) => {
    const picked = viewer.scene.pick(movement.position as Cartesian2)
    const id = picked && typeof picked.id !== 'undefined'
      ? String((picked.id as { name?: string }).name ?? '')
      : ''
    select(entities.has(id) ? id : null)
  }, ScreenSpaceEventType.LEFT_CLICK)

  return {
    entities,
    selected: () => selectedId,
    select,
    setBuildingHeight: (id, metres) => {
      const entity = entities.get(id)
      if (!entity?.polygon) return
      entity.polygon.extrudedHeight = new ConstantProperty(Math.max(1, metres))
      edited.add(id)
      if (selectedId !== id) paint(entity, COLOURS.edited)
      viewer.scene.requestRender()
    },
    setAllHeights: (multiplier) => {
      let changed = 0
      entities.forEach((entity, id) => {
        if (!entity.polygon) return
        const current = entity.polygon.extrudedHeight?.getValue() ?? BASE_HEIGHT
        entity.polygon.extrudedHeight = new ConstantProperty(
          Math.max(1, current * multiplier),
        )
        edited.add(id)
        changed += 1
      })
      if (selectedId) {
        const sel = entities.get(selectedId)
        if (sel) paint(sel, COLOURS.selected)
      }
      viewer.scene.requestRender()
      return changed
    },
    meanHeight: () => {
      const values: number[] = []
      entities.forEach((entity) => {
        const h = entity.polygon?.extrudedHeight?.getValue()
        if (typeof h === 'number') values.push(h)
      })
      return values.length
        ? values.reduce((a, b) => a + b, 0) / values.length
        : 0
    },
    dispose: () => {
      handler.destroy()
      if (!viewer.isDestroyed() && viewer.dataSources.contains(source)) {
        viewer.dataSources.remove(source, true)
      }
    },
  }
}

/**
 * 2.5D footprint overlay used when the Plan Project toggle is on.
 *
 * Reads buildings_hsk.json (396 GeoJSON Polygon features, each with
 * `properties.id` and `properties.height_m`) and renders every footprint as an
 * extruded white polygon with a thin black outline — a planning-document style
 * massing distinct from the textured 3D Tiles mesh it replaces while the
 * toggle is active.
 *
 * Entities, not Primitives: 396 polygons toggle in <5 ms, and the Entity API
 * makes per-building `show = false` trivial, which is what the overlap-hiding
 * step needs.
 */
import {
  Cartesian3,
  Color,
  Entity,
  PolygonHierarchy,
  type Viewer,
} from 'cesium'
import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson'
import { sampleDemHeightClamped } from './demProvider'

type Position2D = [number, number]
type Ring = Position2D[]

function outerRings(geom: Polygon | MultiPolygon): Ring[] {
  if (geom.type === 'Polygon') {
    return [geom.coordinates[0] as Ring]
  }
  return geom.coordinates.map((poly) => poly[0] as Ring)
}

export interface BuildingProps {
  id: string
  height_m: number
  [key: string]: unknown
}

export interface FootprintLayerHandle {
  readonly size: number
  setHiddenIds(ids: ReadonlySet<string>): void
  destroy(): void
}

const FILL = Color.WHITE.withAlpha(0.92)
const OUTLINE = Color.BLACK

export function createFootprintLayer(
  viewer: Viewer,
  buildings: FeatureCollection<Polygon | MultiPolygon, BuildingProps>,
  hiddenIds: ReadonlySet<string>,
): FootprintLayerHandle {
  const entities = new Map<string, Entity>()

  for (const f of buildings.features) {
    const id = f.properties?.id
    const h = Number(f.properties?.height_m)
    if (!id || !Number.isFinite(h) || h <= 0) continue
    const rings = outerRings(f.geometry).filter((r) => r.length >= 3)
    if (rings.length === 0) continue

    // Cesium's PolygonGraphics ignores heightReference once extrudedHeight is
    // set, so the base sits on the ellipsoid (h=0) and buildings sink into the
    // 3-17 m local terrain. Sample the DEM at every outer-ring vertex and use
    // the minimum so sloped sites keep their uphill corner at ground level
    // rather than the floor floating over the downhill one. Clamped sampler
    // handles the few features whose rings extend past the DEM bbox.
    let base = Number.POSITIVE_INFINITY
    for (const ring of rings) {
      for (const [lon, lat] of ring) {
        const z = sampleDemHeightClamped(lon, lat)
        if (z !== null && z < base) base = z
      }
    }
    if (!Number.isFinite(base)) base = 0

    // One Entity per outer ring. MultiPolygons are rare (1/396 at the time of
    // writing) but splitting keeps the ring-per-entity model simple — Cesium's
    // PolygonHierarchy models holes, not disjoint parts.
    rings.forEach((ring, i) => {
      const positions = ring.map(([lon, lat]) =>
        Cartesian3.fromDegrees(lon, lat),
      )
      const entityId = rings.length === 1 ? id : `${id}#${i}`
      const entity = viewer.entities.add({
        id: `footprint:${entityId}`,
        show: !hiddenIds.has(id),
        polygon: {
          hierarchy: new PolygonHierarchy(positions),
          material: FILL,
          outline: true,
          outlineColor: OUTLINE,
          outlineWidth: 2,
          height: base,
          extrudedHeight: base + h,
        },
      })
      entities.set(entityId, entity)
    })
  }
  viewer.scene.requestRender()

  return {
    get size() {
      return entities.size
    },
    setHiddenIds(ids) {
      for (const [entityId, entity] of entities) {
        // MultiPolygon rings get an "#N" suffix; strip it to resolve against
        // hiddenIds which keyed on the original feature id.
        const baseId = entityId.includes('#') ? entityId.split('#')[0] : entityId
        entity.show = !ids.has(baseId)
      }
      viewer.scene.requestRender()
    },
    destroy() {
      for (const entity of entities.values()) {
        viewer.entities.remove(entity)
      }
      entities.clear()
      viewer.scene.requestRender()
    },
  }
}

export function collectIntersectingIds(
  buildings: FeatureCollection<Polygon | MultiPolygon, BuildingProps>,
  footprint: Feature<Polygon> | Polygon,
  intersects: (
    a: Feature<Polygon | MultiPolygon>,
    b: Feature<Polygon> | Polygon,
  ) => boolean,
): Set<string> {
  const hidden = new Set<string>()
  for (const f of buildings.features) {
    const id = f.properties?.id
    if (!id) continue
    try {
      if (intersects(f, footprint)) hidden.add(id)
    } catch {
      // Degenerate polygon from the fuse step; skip rather than kill the layer.
    }
  }
  return hidden
}

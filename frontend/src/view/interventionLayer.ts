/**
 * Intervention feature layer (spec §1.1 `view/interventionLayer.ts`).
 *
 * Discrete features get geometry; continuous fields get raster overlays
 * (ADR-032). Interventions are the discrete case, so they render straight from
 * the GeoJSON FeatureCollection with no coordinate conversion (CLAUDE.md
 * principle 2).
 */
import {
  Color,
  ColorMaterialProperty,
  ConstantProperty,
  GeoJsonDataSource,
  HeightReference,
  type Viewer,
} from 'cesium'

import type { FeatureCollection } from '@/api/types'

/**
 * Attach a FeatureCollection to a viewer. Returns a disposer.
 *
 * **The load is awaited, and the order is load-then-add.** Two things follow from
 * that, both learned the hard way:
 *
 * 1. `GeoJsonDataSource.load()` is async. Adding the source to the viewer first
 *    and loading afterwards does technically work, but it leaves the layer's
 *    contents to arrive whenever the network says so.
 * 2. With `requestRenderMode: true` — which this viewer sets — entities that
 *    appear asynchronously **never paint** until something asks for a frame.
 *    Loading and adding without a final `requestRender()` produced a layer that
 *    was present in the scene graph, reported 7 features in the panel, and drew
 *    absolutely nothing. Same class of bug as the raster overlays.
 *
 * So: load, add, request a frame. And surface a failure rather than swallowing
 * it, because a silently-empty layer looks exactly like a layer with no data.
 */
export async function addInterventions(
  viewer: Viewer,
  collection: FeatureCollection,
): Promise<() => void> {
  // An empty FeatureCollection is a valid, renderable answer — nothing to do.
  if (!collection.features.length) return () => undefined

  const dataSource = new GeoJsonDataSource('interventions')
  try {
    await dataSource.load(collection as unknown as object, { clampToGround: true })
  } catch (err) {
    console.warn('[sandbox] interventions failed to load:', err)
    return () => undefined
  }

  applyStyle(dataSource)

  if (viewer.isDestroyed()) return () => undefined
  await viewer.dataSources.add(dataSource)
  viewer.scene.requestRender()

  return () => {
    if (!viewer.isDestroyed() && viewer.dataSources.contains(dataSource)) {
      viewer.dataSources.remove(dataSource, true)
    }
  }
}


interface StyleSpec {
  color?: string
  opacity?: number
  extrusionHeight?: number
}

/**
 * Apply each feature's `style` to the entity Cesium built from it.
 *
 * `style` is part of the spec's intervention contract (§4.2.3:
 * `{color, opacity, extrusionHeight}`), but it is a **project** property, not a
 * GeoJSON one, so `GeoJsonDataSource` carries it through untouched and renders
 * every feature in Cesium's default yellow. Reading it is what makes the field
 * real rather than decorative -- and it is also the only way a legend can mean
 * anything, since colour is how flood storage is told from tree planting.
 *
 * Unknown colour strings are ignored rather than thrown on: one bad feature
 * should not empty the whole layer.
 */
function applyStyle(dataSource: GeoJsonDataSource): void {
  for (const entity of dataSource.entities.values) {
    const style = entity.properties?.style?.getValue() as StyleSpec | undefined
    if (!style) continue

    let colour: Color | undefined
    if (style.color) {
      const parsed = Color.fromCssColorString(style.color)
      // fromCssColorString returns undefined for an unparseable string rather
      // than throwing, so one bad colour cannot empty the layer.
      if (parsed) colour = parsed.withAlpha(style.opacity ?? 1)
    }

    if (colour) {
      // These are all Property-typed, hence the wrappers. Assigning the raw
      // Color typechecks against nothing useful and silently does nothing.
      if (entity.polygon) {
        entity.polygon.material = new ColorMaterialProperty(colour)
      }
      if (entity.polyline) {
        entity.polyline.material = new ColorMaterialProperty(colour)
      }
      if (entity.point) {
        entity.point.color = new ConstantProperty(colour)
      }
    }

    // Extrusion is set from absolute metres, so the polygon must NOT also be
    // clamped to ground -- otherwise the two offsets compound and the block
    // floats. Polygons without an extrusion height stay clamped.
    const height = style.extrusionHeight
    if (entity.polygon) {
      if (height) {
        entity.polygon.extrudedHeight = new ConstantProperty(height)
        entity.polygon.heightReference = new ConstantProperty(
          HeightReference.NONE,
        )
      } else {
        entity.polygon.heightReference = new ConstantProperty(
          HeightReference.CLAMP_TO_GROUND,
        )
      }
    }
  }
}

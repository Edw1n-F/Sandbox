/**
 * Intervention feature layer (spec §1.1 `view/interventionLayer.ts`).
 *
 * Discrete features get geometry; continuous fields get raster overlays
 * (ADR-032). Interventions are the discrete case, so they render straight from
 * the GeoJSON FeatureCollection with no coordinate conversion (CLAUDE.md
 * principle 2).
 */
import { GeoJsonDataSource, type Viewer } from 'cesium'

import type { FeatureCollection } from '@/api/types'

/** Attach a FeatureCollection to a viewer. Returns a disposer. */
export function addInterventions(
  viewer: Viewer,
  collection: FeatureCollection,
): () => void {
  // An empty FeatureCollection is a valid, renderable answer — do nothing.
  if (!collection.features.length) return () => undefined

  const dataSource = new GeoJsonDataSource('interventions')
  void dataSource.load(collection as unknown as object, { clampToGround: true })
  void viewer.dataSources.add(dataSource)

  return () => {
    if (viewer.dataSources.contains(dataSource)) {
      viewer.dataSources.remove(dataSource, true)
    }
  }
}

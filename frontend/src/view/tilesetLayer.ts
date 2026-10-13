/**
 * 3D Tiles layer — real textured building meshes and terrain from 6-NW-12D.
 *
 * The new city_model and terrain(TB) tilesets carry no root.transform (their
 * vertices are in HK1980 metre grid).  Pass a modelMatrix from hk1980Transform
 * so Cesium positions them correctly in ECEF.
 */
import { Cesium3DTileset, Matrix4, type Viewer } from 'cesium'

import { useTileLoadStore, type TilesetName } from '@/state/tileLoadStore'

export interface TilesetHandle {
  tileset: Cesium3DTileset
  dispose: () => void
}

export interface TilesetOptions {
  /** 4×4 model matrix applied after load (e.g. HK1980 → ECEF). */
  modelMatrix?: Matrix4
  /**
   * Maximum screen-space error threshold.
   * 16 is a good starting point for multi-LOD tilesets; lower values load more
   * detail, higher values load less.  The old single-tile default was 1.
   */
  maximumScreenSpaceError?: number
  /** Reported to the loading screen. */
  name?: TilesetName
}

/**
 * Load a 3D Tiles tileset from a URL and add it to the scene.
 *
 * Failure is logged rather than thrown: a missing or malformed tileset must not
 * take the scene down, exactly like a missing city model or waterbody layer.
 */
export async function add3DTileset(
  viewer: Viewer,
  url: string,
  options: TilesetOptions = {},
): Promise<TilesetHandle | null> {
  if (viewer.isDestroyed()) return null

  console.log('[3dtiles] loading tileset from', url)

  const { modelMatrix, maximumScreenSpaceError = 16, name } = options
  const report = name ? useTileLoadStore.getState() : null
  if (name) report?.set(name, 'loading')

  try {
    const tileset = await Cesium3DTileset.fromUrl(url, {
      maximumScreenSpaceError,
      // Steady detail beats fast detail here. Dynamic / foveated SSE and culling
      // requests while moving all change the level of detail *as the camera moves*,
      // which reads as the buildings shifting and re-texturing during a zoom. With
      // them off the tile set only changes when the view settles.
      dynamicScreenSpaceError: false,
      foveatedScreenSpaceError: false,
      cullRequestsWhileMoving: false,
      // Load a coarse version of the first view straight away, then refine, so the
      // loading screen can lift on a recognisable scene instead of a full-detail one.
      progressiveResolutionHeightFraction: 0.5,
      // Keep a parent visible until all its children are ready (no holes while
      // refining) and do not skip levels, so detail steps up one tier at a time.
      skipLevelOfDetail: false,
      // The 300 MB city model needs room before tiles get evicted and re-fetched,
      // which reads as flicker when orbiting.
      cacheBytes: 768 * 1024 * 1024,
      maximumCacheOverflowBytes: 512 * 1024 * 1024,
    })
    if (viewer.isDestroyed()) {
      tileset.destroy()
      return null
    }

    if (name) {
      // `initialTilesLoaded` fires once, when everything the first view needs is in.
      tileset.initialTilesLoaded.addEventListener(() => useTileLoadStore.getState().set(name, 'ready'))
      tileset.loadProgress.addEventListener((pending: number, processing: number) =>
        useTileLoadStore.getState().setPending(name, pending + processing),
      )
    }

    if (modelMatrix) {
      tileset.modelMatrix = Matrix4.clone(modelMatrix)
    }

    tileset.allTilesLoaded.addEventListener(() => {
      console.log('[3dtiles] all tiles loaded')
      if (!viewer.isDestroyed()) viewer.scene.requestRender()
    })

    // requestRenderMode suppresses automatic redraws; each batch of tiles that
    // arrives needs an explicit frame request or it sits in the primitive list
    // but never paints.
    tileset.tileLoad.addEventListener(() => {
      if (!viewer.isDestroyed()) viewer.scene.requestRender()
    })

    tileset.tileFailed.addEventListener((event) => {
      console.error('[3dtiles] tile failed:', event)
    })

    viewer.scene.primitives.add(tileset)
    viewer.scene.requestRender()
    console.log('[3dtiles] tileset added to scene')
    return {
      tileset,
      dispose: () => {
        if (name) useTileLoadStore.getState().reset(name)
        if (!viewer.isDestroyed()) {
          viewer.scene.primitives.remove(tileset)
        } else {
          tileset.destroy()
        }
      },
    }
  } catch (err) {
    console.warn('[3dtiles] failed to load tileset:', err)
    if (name) useTileLoadStore.getState().set(name, 'failed')
    return null
  }
}

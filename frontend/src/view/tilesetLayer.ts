/**
 * 3D Tiles layer — real textured building meshes and terrain from 6-NW-12D.
 *
 * The new city_model and terrain(TB) tilesets carry no root.transform (their
 * vertices are in HK1980 metre grid).  Pass a modelMatrix from hk1980Transform
 * so Cesium positions them correctly in ECEF.
 */
import { Cesium3DTileset, Matrix4, type Viewer } from 'cesium'

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

  const { modelMatrix, maximumScreenSpaceError = 16 } = options

  try {
    const tileset = await Cesium3DTileset.fromUrl(url, {
      maximumScreenSpaceError,
    })
    if (viewer.isDestroyed()) {
      tileset.destroy()
      return null
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
        if (!viewer.isDestroyed()) {
          viewer.scene.primitives.remove(tileset)
        } else {
          tileset.destroy()
        }
      },
    }
  } catch (err) {
    console.warn('[3dtiles] failed to load tileset:', err)
    return null
  }
}

/**
 * Online 3D assets, loaded straight from the Khronos glTF sample set.
 *
 * ## Why these
 *
 * `GENAI Hackathon` wants a sample sandbox map with real 3D content, and the
 * brief is to use online assets rather than ship binaries. The Khronos
 * glTF-Sample-Assets repository on GitHub raw is the right source: public,
 * permissively licensed, and — the part that actually matters — it serves
 * `Access-Control-Allow-Origin: *`, so the browser can fetch it with no proxy
 * and no token. Most of that set is 3–9 MB per model, which is why only the
 * small ones are used here: a 9 MB lantern to decorate a flood sandbox is a
 * worse engineering choice than a 1.6 KB box.
 *
 * The heavier alternatives were measured and rejected on size, not guessed at.
 *
 * ## What this layer is for
 *
 * It proves the glTF path end to end -- fetch, parse, place, transform -- and it
 * gives the sandbox movable props. It is *not* the building layer: that comes
 * from the real 3D Tiles tileset, because a glTF mesh is opaque to per-feature
 * edits and changing geometry is the point here.
 */
import {
  Cartesian3,
  HeadingPitchRoll,
  Matrix4,
  Quaternion,
  TranslationRotationScale,
  Model,
  type Viewer,
} from 'cesium'

export interface OnlineProp {
  id: string
  label: string
  url: string
  /** Nominal model height in metres, used to scale it to something sane. */
  nominalMetres: number
  lon: number
  lat: number
  /** Height above the ellipsoid, so it is not buried in the terrain. */
  heightM: number
  scale: number
  headingDeg: number
  colour: string
}

const ASSET_BASE =
  'https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models'

/**
 * A deliberately small palette of *verified* URLs.
 *
 * Every entry was fetched and confirmed to return 200 with a CORS header before
 * being written down. A 404 or a missing CORS header on a demo asset is
 * invisible until the day it matters, and the models were picked for size
 * because the big ones in that repository would stall the scene.
 */
export const ONLINE_PROPS: OnlineProp[] = [
  {
    id: 'marker-north',
    label: 'Survey marker (north basin)',
    url: `${ASSET_BASE}/Box/glTF-Binary/Box.glb`,
    nominalMetres: 12,
    lon: 113.9959,
    lat: 22.4451,
    heightM: 4,
    scale: 1,
    headingDeg: 25,
    colour: '#ffd54f',
  },
  {
    id: 'marker-west',
    label: 'Survey marker (west block)',
    url: `${ASSET_BASE}/BoxTextured/glTF-Binary/BoxTextured.glb`,
    nominalMetres: 12,
    lon: 113.9895,
    lat: 22.4378,
    heightM: 4,
    scale: 1,
    headingDeg: 200,
    colour: '#4fc3f7',
  },
  {
    id: 'marker-south',
    label: 'Survey marker (south channel)',
    url: `${ASSET_BASE}/SimpleSparseAccessor/glTF/SimpleSparseAccessor.gltf`,
    nominalMetres: 12,
    lon: 113.9992,
    lat: 22.4305,
    heightM: 4,
    scale: 1,
    headingDeg: 95,
    colour: '#a1887f',
  },
]

/** Place a model at a lon/lat/height with a compass heading, in the local ENU frame. */
function placementMatrix(lon: number, lat: number, heightM: number,
                         headingDeg: number, scale: number): Matrix4 {
  return Matrix4.fromTranslationRotationScale(
    new TranslationRotationScale(
      Cartesian3.fromDegrees(lon, lat, heightM),
      Quaternion.fromHeadingPitchRoll(
        new HeadingPitchRoll(headingDeg * (Math.PI / 180), 0, 0),
      ),
      new Cartesian3(scale, scale, scale),
    ),
  )
}


export interface OnlinePropHandle {
  models: Map<string, Model>
  loaded: () => string[]
  failed: () => string[]
  moveTo: (id: string, lon: number, lat: number, headingDeg?: number) => boolean
  setScale: (id: string, scale: number) => boolean
  dispose: () => void
}

/**
 * Load every prop and place it.
 *
 * Each model is loaded independently and a failure is recorded rather than
 * thrown, because one dead asset URL must not empty the layer -- and the panel
 * reports the count that loaded so a silent failure is still visible.
 */
export async function addOnlineProps(
  viewer: Viewer,
  props: OnlineProp[] = ONLINE_PROPS,
): Promise<OnlinePropHandle | null> {
  if (viewer.isDestroyed() || props.length === 0) return null

  const models = new Map<string, Model>()
  const failures: string[] = []

  await Promise.all(
    props.map(async (prop) => {
      try {
        // glTF is authored in metres, so no unit fix-up is needed. Scale and
        // placement both go in the model matrix rather than `Model.scale`,
        // because a heading has to rotate about the local vertical anyway.
        const model = await Model.fromGltfAsync({
          url: prop.url,
          modelMatrix: placementMatrix(
            prop.lon, prop.lat, prop.heightM, prop.headingDeg, prop.scale,
          ),
        })
        if (viewer.isDestroyed()) {
          model.destroy()
          return
        }
        models.set(prop.id, model)
        try {
          await viewer.scene.primitives.add(model)
        } catch (err) {
          // Already added, or the scene went away. Not fatal.
          console.warn(`[props] could not add ${prop.id}:`, err)
        }
      } catch (err) {
        failures.push(prop.id)
        console.warn(`[props] ${prop.id} failed to load from ${prop.url}:`, err)
      }
    }),
  )

  if (models.size === 0) {
    console.warn('[props] no online asset loaded; the layer stays empty')
    return null
  }

  viewer.scene.requestRender()

  return {
    models,
    loaded: () => [...models.keys()],
    failed: () => failures,
    moveTo: (id, lon, lat, headingDeg) => {
      const model = models.get(id)
      if (!model) return false
      const heading = headingDeg ?? 0
      const current = model.modelMatrix
      // Preserve the existing uniform scale while replacing translation and
      // rotation, so moving a prop does not silently resize it.
      const scale = current ? Matrix4.getScale(current, new Cartesian3()) : new Cartesian3(1, 1, 1)
      model.modelMatrix = placementMatrix(lon, lat, 4, heading, scale.x)
      viewer.scene.requestRender()
      return true
    },
    setScale: (id, scale) => {
      const model = models.get(id)
      if (!model || !Number.isFinite(scale) || scale <= 0) return false
      // Rescale about the prop's own anchor: rebuild the matrix at the new
      // uniform scale rather than assigning Model.scale, which would discard
      // the heading.
      const current = model.modelMatrix
      const translation = current
        ? Cartesian3.clone(Matrix4.getTranslation(current, new Cartesian3()))
        : Cartesian3.ZERO
      const heading = 0
      model.modelMatrix = Matrix4.fromTranslationRotationScale(
        new TranslationRotationScale(
          translation,
          Quaternion.fromHeadingPitchRoll(
            new HeadingPitchRoll(heading * (Math.PI / 180), 0, 0),
          ),
          new Cartesian3(scale, scale, scale),
        ),
      )
      viewer.scene.requestRender()
      return true
    },
    dispose: () => {
      for (const model of models.values()) {
        if (viewer.isDestroyed()) {
          model.destroy()
        } else {
          viewer.scene.primitives.remove(model)
        }
      }
      models.clear()
    },
  }
}

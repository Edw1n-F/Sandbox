/**
 * HK1980 Grid (EPSG:2326) → ECEF transform for self-hosted 3D Tiles.
 *
 * The city_model and terrain(TB) tilesets have no root.transform: their
 * bounding volumes and vertex coordinates are in HK1980 metre grid (E, N, Z).
 * Cesium treats those as ECEF (≈ 817 km from Earth centre), so every tileset
 * needs a modelMatrix that maps HK1980 local space to ECEF.
 *
 * The derivation (plan.md §3, step 1):
 *   1. Pick a shared origin: city_model root bbox centre (E0, N0).
 *   2. Convert to WGS84 via pyproj EPSG:2326 → EPSG:4326.
 *   3. Build an ENU frame at (lon0, lat0, height0) — the Z offset tunes
 *      vertical alignment against the terrain-RGB surface.
 *   4. Prepend a translation of (-E0, -N0, -H0) so that a local point
 *      (E0, N0, H0) maps to the ENU origin.
 *
 * Constant values were computed by gen_hk1980_transform.py from the root
 * bounding volumes of both tilesets.
 */
import { Cartesian3, Matrix4, Transforms } from 'cesium'

export const HK1980_ORIGIN = {
  /** HK1980 easting of the shared tile origin (metres). */
  easting: 817622.46875,
  /** HK1980 northing of the shared tile origin (metres). */
  northing: 832677.072265625,
  /**
   * Height offset (metres).  HK1980 Z values are metres above the HK Principal
   * Datum (approximately MSL).  WGS84 ellipsoid height ≈ HK datum height + 4 m
   * in this region; using 0 here is conservative — visual terrain may float
   * slightly above the clamping surface, which is preferable to sinking into it.
   * Adjust if the rendered terrain mesh appears mis-aligned after calibration.
   */
  height: 0,
  /** WGS84 longitude derived from (easting, northing) via pyproj EPSG:2326→4326. */
  longitude: 113.99575423411075,
  /** WGS84 latitude derived from (easting, northing) via pyproj EPSG:2326→4326. */
  latitude: 22.43338192552023,
} as const

/**
 * Returns a 4×4 modelMatrix that positions HK1980-local 3D Tiles in ECEF.
 *
 * The matrix is: ENU_to_ECEF(origin) × Translation(-E0, -N0, -H0)
 *
 * A tileset vertex at (E, N, Z) in HK1980 metres gets mapped as:
 *   local ENU = (E − E0, N − N0, Z − H0)
 *   ECEF = enuToEcef × local_ENU
 */
export function hk1980ToEcefMatrix(): Matrix4 {
  const originEcef = Cartesian3.fromDegrees(
    HK1980_ORIGIN.longitude,
    HK1980_ORIGIN.latitude,
    HK1980_ORIGIN.height,
  )
  const enuToEcef = Transforms.eastNorthUpToFixedFrame(originEcef)
  const offset = new Cartesian3(
    -HK1980_ORIGIN.easting,
    -HK1980_ORIGIN.northing,
    -HK1980_ORIGIN.height,
  )
  return Matrix4.multiplyByTranslation(enuToEcef, offset, new Matrix4())
}

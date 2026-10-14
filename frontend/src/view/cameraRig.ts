/**
 * Camera framing and navigation for a 3D district.
 *
 * ## The bug this file exists to fix
 *
 * `camera.flyTo({ destination: rectangle })` frames a `Rectangle` by putting the
 * camera directly above its centre, looking straight down. Cesium offers no
 * alternative: a rectangle carries no orientation, so the fit is always nadir. For a
 * flood-exposure raster that is the right answer. For a city it is the wrong
 * one -- pitch -90 deg over real buildings on 139 m of relief renders a skyline as
 * flat rectangles, which is exactly how the sandbox read before this file existed:
 * a flat map with no visible third dimension in it.
 *
 * So the bbox is framed as a bounding sphere instead, with a chosen heading and a
 * pitch shallow enough to show facades and steep enough to keep the plan readable.
 *
 * ## One placement routine, on purpose
 *
 * `placeCamera` is shared by the bbox framing, the scroll pan and the orbit buttons.
 * The pan was out by a factor of ~57 when it computed its own world-space offset, and
 * four different ways of translating the camera directly each landed somewhere other
 * than the requested point. Displacing the ground *target* and re-placing the camera
 * relative to it reuses geometry that the framing check already pins, rather than
 * deriving a second, subtly different version of it.
 */
import {
  CameraEventType,
  Cartesian2,
  Cartesian3,
  Ellipsoid,
  KeyboardEventModifier,
  Math as CesiumMath,
  Matrix4,
  Transforms,
  type Viewer,
} from 'cesium'

/** Metres per degree of latitude. Used to size the study-area framing sphere. */
const M_PER_DEG_LAT = 110_574

const DEG = Math.PI / 180

/**
 * Where the app opens: south-south-west of the site, looking back over it towards the
 * northern hills. Off-axis deliberately -- dead-on north is what a default screenshot
 * takes, and the relief reads better with some obliquity.
 */
export const DEFAULT_HEADING_DEG = 335

/**
 * -33 deg. Shallow enough that 34 m envelopes and 139 m of relief both read as
 * volumes; steep enough that the plan stays legible, which matters because the
 * wet-grid and heatmap rasters are why half the users are here.
 */
export const DEFAULT_PITCH_DEG = -33

/**
 * Pitch limits for orbiting.
 *
 * The near-horizontal end stops the camera swinging up past the district, where
 * `enableCollisionDetection` starts shoving it around. The steep end stops short of
 * nadir, because straight down is a different view with its own button: a rotation
 * control that lands you in it by accident just makes both harder to use.
 */
export const MIN_PITCH_DEG = -88
export const MAX_PITCH_DEG = -8

/**
 * The fixed point the camera frames and orbits: the centre of the whole sandbox terrain
 * (the terrain tileset's bounding-sphere centre, height included). Without it the orbit
 * pivots on whatever sits under the screen centre, which after the side-panel shift is
 * a corner-ish point, so the model swings around instead of turning in place.
 */
let sandboxPivot: Cartesian3 | null = null
let sandboxInsetLeft = 0

export function setSandboxPivot(point: Cartesian3 | null): void {
  sandboxPivot = point ? Cartesian3.clone(point) : null
}

/** Pixels of left panel to keep clear when orbiting, same meaning as `FrameOptions.insetLeft`. */
export function setSandboxInset(px: number): void {
  sandboxInsetLeft = Math.max(0, px)
}

export interface Extent {
  west: number
  south: number
  east: number
  north: number
}

export interface FrameOptions {
  /**
   * Pixels of canvas covered by a panel on the left. The study area is shifted into
   * the middle of what is actually visible rather than the middle of the canvas.
   * Clamped, so a narrow window cannot push the target off screen.
   */
  insetLeft?: number
  /** Metres of vertical extent to keep in frame beyond the bbox itself. */
  reliefM?: number
  /** Multiplier on the fit distance, so nothing kisses the viewport edge. */
  margin?: number
  headingDeg?: number
  pitchDeg?: number
  duration?: number
}

/** Half the bbox diagonal, in metres, floored and grown by the relief. */
export function extentRadiusM(extent: Extent, reliefM = 0): number {
  const midLat = (extent.north + extent.south) / 2
  const mPerDegLon = 111_320 * Math.cos(midLat * DEG)
  const widthM = (extent.east - extent.west) * mPerDegLon
  const heightM = (extent.north - extent.south) * M_PER_DEG_LAT
  // Floored so a single-building scene is not framed from inside itself, and grown by
  // the relief so the hills stay inside the frame.
  return Math.max(0.5 * Math.hypot(widthM, heightM), reliefM * 0.5, 60)
}

/**
 * Cesium applies `fov` to the *larger* screen dimension, so the horizontal half-angle
 * is always `fov / 2` and only the vertical one narrows, on a landscape canvas.
 */
function verticalHalfTangent(viewer: Viewer): number {
  const frustum = viewer.camera.frustum as unknown as {
    fov: number
    aspectRatio?: number
  }
  const canvas = viewer.scene.canvas
  const aspect =
    frustum.aspectRatio ??
    (canvas && canvas.height > 0 ? canvas.width / canvas.height : 1)
  const tanHalfH = Math.tan(frustum.fov / 2)
  return aspect >= 1 ? tanHalfH / aspect : tanHalfH
}

/** World metres covered by one screen pixel at the given distance. */
function metresPerPixel(viewer: Viewer, distance: number): number {
  const height = Math.max(1, viewer.scene.canvas?.clientHeight ?? 1)
  return (2 * distance * verticalHalfTangent(viewer)) / height
}

/**
 * Distance at which a sphere of `radius` fits this viewer's frustum.
 *
 * Written out rather than reusing Cesium's private helper, with one change: a sphere is
 * tangent to the frustum planes at `r / sin(theta)`, not at `r / tan(theta)` as
 * `flyToBoundingSphere` uses. The honest value is the larger of the two, so nothing
 * clips at the corners.
 */
export function fitDistance(viewer: Viewer, radius: number): number {
  const frustum = viewer.camera.frustum as unknown as { fov: number }
  const tanHalfH = Math.tan(frustum.fov / 2)
  return radius / Math.sin(Math.min(tanHalfH, verticalHalfTangent(viewer)))
}

/**
 * Put the camera at `range` from `target`, looking at it.
 *
 * Offset from the target back to the camera, in local east-north-up. The view
 * direction for a heading/pitch pair is (sin h, cos h, sin p) in ENU: heading is a
 * compass bearing, so 0 is north and sin/cos are *not* interchangeable. Getting that
 * backwards does not look broken -- it is still a consistent oblique view -- it just
 * quietly views the site from the opposite side to the one the heading names, which is
 * the sort of thing nobody notices until the constant and the picture disagree.
 */
function placeCamera(
  viewer: Viewer,
  target: Cartesian3,
  heading: number,
  pitch: number,
  range: number,
  duration: number,
  insetLeft = 0,
): void {
  const frame = Transforms.eastNorthUpToFixedFrame(target, Ellipsoid.WGS84)
  const back = new Cartesian3(
    -range * Math.sin(heading) * Math.cos(pitch),
    -range * Math.cos(heading) * Math.cos(pitch),
    -range * Math.sin(pitch),
  )
  const destination = Matrix4.multiplyByPoint(frame, back, new Cartesian3())

  // Derive direction and up from the destination that was just computed, rather than
  // handing over the camera's current orientation.
  //
  // This is the part that matters, and it was found the hard way. Passing the
  // camera's *existing* direction/up alongside a displaced destination is
  // self-contradictory -- the orientation no longer describes the geometry -- and
  // Cesium resolves the contradiction by re-deriving the position, which silently
  // threw away almost the whole displacement. Measured: a 2 km pan arrived as 39 m,
  // the same wrong answer from five different camera APIs, because all five were being
  // handed the same inconsistent pair. `frameExtent` always derived its orientation
  // from its own destination and always landed exactly; this is that, hoisted.
  const direction = Cartesian3.normalize(
    Cartesian3.subtract(target, destination, new Cartesian3()),
    new Cartesian3(),
  )
  const up = Matrix4.multiplyByPointAsVector(frame, Cartesian3.UNIT_Z, new Cartesian3())

  // Keep the pivot centred in the strip of canvas the left panel leaves free. Pure
  // translation, same as `frameExtent`, so orientation and the pivot distance are untouched.
  const inset = Math.min(insetLeft, (viewer.scene.canvas?.width ?? 0) * 0.4)
  if (inset > 0) {
    const right = Cartesian3.normalize(
      Cartesian3.cross(direction, up, new Cartesian3()),
      new Cartesian3(),
    )
    Cartesian3.multiplyByScalar(right, -(inset / 2) * metresPerPixel(viewer, range), right)
    Cartesian3.add(destination, right, destination)
  }

  if (duration <= 0) {
    viewer.camera.setView({ destination, orientation: { direction, up } })
  } else {
    viewer.camera.flyTo({ destination, orientation: { direction, up }, duration })
  }
}

/**
 * Frame `extent` from the given heading and pitch.
 *
 * Returns the destination it flew to, so a caller can assert on it -- which is how the
 * nadir regression is kept fixed: the orientation comes from the arguments, never from
 * the bbox, so a change here cannot silently flatten the view again.
 */
export function frameExtent(
  viewer: Viewer,
  extent: Extent,
  options: FrameOptions = {},
): Cartesian3 {
  const heading = CesiumMath.toRadians(options.headingDeg ?? DEFAULT_HEADING_DEG)
  const pitch = CesiumMath.toRadians(options.pitchDeg ?? DEFAULT_PITCH_DEG)
  const duration = options.duration ?? 1.2

  const radius = extentRadiusM(extent, options.reliefM ?? 0)
  const range = fitDistance(viewer, radius) * (options.margin ?? 1.06)

  // The look-at point sits on the ellipsoid at the bbox centre, lifted by half the
  // relief so the camera ends up above the hills rather than inside them.
  const target =
    sandboxPivot ??
    Cartesian3.fromDegrees(
      (extent.west + extent.east) / 2,
      (extent.south + extent.north) / 2,
      (options.reliefM ?? 0) / 2,
    )
  const frame = Transforms.eastNorthUpToFixedFrame(target, Ellipsoid.WGS84)
  const back = new Cartesian3(
    -range * Math.sin(heading) * Math.cos(pitch),
    -range * Math.cos(heading) * Math.cos(pitch),
    -range * Math.sin(pitch),
  )
  const destination = Matrix4.multiplyByPoint(frame, back, new Cartesian3())

  // Look direction and up, taken from the *unshifted* geometry.
  const direction = Cartesian3.normalize(
    Cartesian3.subtract(target, destination, new Cartesian3()),
    new Cartesian3(),
  )
  const up = Matrix4.multiplyByPointAsVector(frame, Cartesian3.UNIT_Z, new Cartesian3())

  // Slide the camera left so the study area lands in the middle of the visible strip
  // rather than the middle of the canvas. Translation only: the orientation above is
  // reused unchanged, so this cannot skew the perspective.
  const inset = Math.min(options.insetLeft ?? 0, (viewer.scene.canvas?.width ?? 0) * 0.4)
  if (inset > 0) {
    const right = Cartesian3.normalize(
      Cartesian3.cross(direction, up, new Cartesian3()),
      new Cartesian3(),
    )
    const shift = (inset / 2) * metresPerPixel(viewer, range)
    Cartesian3.multiplyByScalar(right, -shift, right)
    Cartesian3.add(destination, right, destination)
  }

  if (duration <= 0) {
    viewer.camera.setView({ destination, orientation: { direction, up } })
  } else {
    viewer.camera.flyTo({ destination, orientation: { direction, up }, duration })
  }
  return destination
}

/** Sight and feel for the navigation controls, in one place. */
export function tuneNavigation(viewer: Viewer): void {
  const controller = viewer.scene.screenSpaceCameraController
  // 40 m: close enough to read one envelope's facade grid, far enough that the near
  // plane does not end up inside a building.
  controller.minimumZoomDistance = 40
  // 40 km: the study area is 3 km across, so this is "lost it" rather than a limit.
  controller.maximumZoomDistance = 40_000
  // Stops the camera dropping under the terrain on a drag, which on a 139 m relief
  // surface is a very reachable accident.
  controller.enableCollisionDetection = true
  controller.enableTilt = true
}

/**
 * Rebind the mouse/trackpad so the gestures people reach for do what they expect.
 *
 * | gesture       | stock behaviour | after this |
 * |---------------|-----------------|------------|
 * | left-drag     | pan + spin      | pan + spin (move the slab) |
 * | right-drag    | zoom            | **tilt** |
 * | middle-drag   | orbit           | tilt |
 * | wheel / 2-finger trackpad drag | zoom | **tilt** |
 * | pinch / ctrl+scroll | zoom    | zoom |
 *
 * Tilt is the gesture a trackpad user reaches for first (two-finger drag) and
 * the one a mouse user tries second (right-drag, since left is already "move").
 * Cesium's stock right-drag zooms, which duplicated the wheel and the buttons;
 * it now tilts instead. Plain wheel (which on a trackpad *is* the two-finger
 * drag) is mapped to tilt too, so the same gesture works with or without a
 * modifier. Pinch and ctrl+scroll stay zoom.
 *
 * ## Why right-drag is taken off the rotate list
 *
 * In Cesium 3D there is no separate translate handler: `rotateEventTypes` is
 * *the drag gesture*, and panning is what `spin3D` does as part of rotating. So
 * left-drag stays on that list (it is the "move the slab" gesture the user
 * confirmed works); right-drag is removed so it can own tilt outright instead
 * of tilting *and* orbiting at once.
 */
export function bindOrbitGestures(viewer: Viewer): void {
  const controller = viewer.scene.screenSpaceCameraController
  controller.rotateEventTypes = [CameraEventType.LEFT_DRAG]
  // RIGHT_DRAG and MIDDLE_DRAG both tilt. PINCH is left on zoom (a two-finger
  // pinch is a zoom gesture, not a drag), and CTRL+LEFT_DRAG is dropped so a
  // stray modifier does not silently change what the drag does.
  controller.tiltEventTypes = [
    CameraEventType.RIGHT_DRAG,
    CameraEventType.MIDDLE_DRAG,
  ]
  controller.zoomEventTypes = [
    CameraEventType.PINCH,
    { eventType: CameraEventType.WHEEL, modifier: KeyboardEventModifier.CTRL },
  ]
  // Faster zoom for what remains. Cesium's default is 5, and the district is 3 km
  // across, so at the stock rate a pinch took several flicks to change scale usefully.
  controller.zoomFactor = 12

  // Two-finger trackpad drag → tilt (vertical) + rotate (horizontal). A
  // trackpad two-finger drag arrives as a `wheel` event carrying both deltaX
  // and deltaY, so a single diagonal drag can tilt and rotate at once: up/down
  // tilts, left/right orbits. A mouse scroll wheel tilts too (deltaY only).
  // ctrl+wheel is left for Cesium's zoom binding above (the early return lets
  // that through). `orbitBy(..., 0)` is an instant `setView`, not a `flyTo`,
  // so a stream of wheel events does not queue animations. `Math.sign` gives a
  // fixed step per notch regardless of deltaY magnitude, which varies by device.
  viewer.scene.canvas?.addEventListener(
    'wheel',
    (event: WheelEvent) => {
      if (event.ctrlKey) return
      event.preventDefault()
      const { tiltDeg, rotateDeg } = wheelToOrbit(event.deltaX, event.deltaY, event.deltaMode)
      if (tiltDeg !== 0 || rotateDeg !== 0) orbitBy(viewer, rotateDeg, tiltDeg, 0)
    },
    { passive: false },
  )
}

/** Degrees of camera motion per CSS pixel of wheel delta. Tuned so one full
 *  mouse-wheel notch (~100 px) is about the old 5 deg, but a trackpad swipe --
 *  hundreds of tiny events -- adds up to something proportionate to the
 *  distance the fingers actually travelled. */
const TILT_DEG_PER_PX = 0.05
const ROTATE_DEG_PER_PX = 0.12
/** The most a single event may move the camera. A fast flick can report a delta
 *  of several hundred px in one event; unbounded, that is a lurch. */
const MAX_STEP_DEG = 8
/** A cross-axis delta this small is the fingers drifting, not a decision.
 *  Without it a purely vertical swipe also spins the view. */
const AXIS_DEAD_ZONE_PX = 2
/** `WheelEvent.deltaMode`: 0 pixels, 1 lines, 2 pages. */
const LINE_PX = 16
const PAGE_PX = 400

/**
 * Turn one wheel event into a camera move.
 *
 * Exported and pure so the sensitivity can be tested rather than felt: the old
 * handler used `Math.sign` to give a fixed step per event, which is right for a
 * mouse (one notch, one event) and badly wrong for a trackpad, which streams
 * ~60 events a second plus a momentum tail. One gentle swipe became dozens of
 * 5 deg steps and hit the pitch clamp at once. Scaling by the real delta makes
 * the motion follow the fingers instead.
 *
 * The dominant axis wins: if one axis is more than twice the other, the weaker
 * one is zeroed. Trackpads never report an exact zero on the axis you did not
 * mean, so without this a vertical swipe also rotates.
 */
export function wheelToOrbit(
  deltaX: number,
  deltaY: number,
  deltaMode = 0,
): { tiltDeg: number; rotateDeg: number } {
  const scale = deltaMode === 1 ? LINE_PX : deltaMode === 2 ? PAGE_PX : 1
  let dx = deltaX * scale
  let dy = deltaY * scale
  if (Math.abs(dx) < AXIS_DEAD_ZONE_PX) dx = 0
  if (Math.abs(dy) < AXIS_DEAD_ZONE_PX) dy = 0
  if (Math.abs(dx) > 2 * Math.abs(dy)) dy = 0
  else if (Math.abs(dy) > 2 * Math.abs(dx)) dx = 0
  const clamp = (v: number) => Math.max(-MAX_STEP_DEG, Math.min(MAX_STEP_DEG, v))
  return {
    // Scrolling "down" (positive deltaY) tilts toward the horizon, matching the
    // sign the previous handler used.
    tiltDeg: clamp(-dy * TILT_DEG_PER_PX) || 0,
    rotateDeg: clamp(dx * ROTATE_DEG_PER_PX) || 0,
  }
}

/** The point the camera is looking at, as close to the frame centre as we can get. */
function viewCentre(viewer: Viewer): { point: Cartesian3; range: number } {
  const camera = viewer.camera
  if (sandboxPivot) {
    const distance = Cartesian3.distance(camera.positionWC, sandboxPivot)
    return { point: sandboxPivot, range: Math.max(distance, 1) }
  }
  const canvas = viewer.scene.canvas
  const middle = new Cartesian2(
    Math.max(1, canvas?.clientWidth ?? 1) / 2,
    Math.max(1, canvas?.clientHeight ?? 1) / 2,
  )
  // The middle of the frame, which is the thing being orbited around. Picking the
  // ellipsoid rather than the terrain is deliberate: it is always defined, so the orbit
  // cannot fail halfway, and the error is bounded by the relief -- 141 m here.
  const picked = camera.pickEllipsoid(middle, Ellipsoid.WGS84)
  // Looking at the horizon there is nothing under the crosshair, so orbit about a point
  // along the view ray instead. Orbiting still works; there is simply no ground to
  // hold still.
  const point =
    picked ??
    Cartesian3.add(
      camera.positionWC,
      Cartesian3.multiplyByScalar(camera.directionWC, 4000, new Cartesian3()),
      new Cartesian3(),
    )
  const range = Math.max(
    Cartesian3.magnitude(
      Cartesian3.subtract(camera.positionWC, point, new Cartesian3()),
    ),
    1,
  )
  return { point, range }
}

/**
 * Orbit the camera around whatever it is looking at.
 *
 * Rotates *about the subject* rather than spinning in place, because spinning in place
 * throws the subject out of frame -- fine for a landscape, useless for inspecting a
 * building. Reuses `placeCamera` so an orbit and the 3D-view button cannot disagree
 * about where the camera ends up.
 */
export function orbitBy(
  viewer: Viewer,
  headingDeg: number,
  pitchDeg: number,
  duration = 0.3,
): void {
  const camera = viewer.camera
  const heading = camera.heading + CesiumMath.toRadians(headingDeg)
  const pitch = CesiumMath.clamp(
    camera.pitch + CesiumMath.toRadians(pitchDeg),
    CesiumMath.toRadians(MIN_PITCH_DEG),
    CesiumMath.toRadians(MAX_PITCH_DEG),
  )
  const { point, range } = viewCentre(viewer)
  placeCamera(viewer, point, heading, pitch, range, duration, sandboxPivot ? sandboxInsetLeft : 0)
}

/**
 * Move the camera towards or away from what it is looking at.
 *
 * A distance multiply rather than `camera.zoomIn`, which is a per-tick delta: under
 * `requestRenderMode` there may be no tick to apply it on, so it does nothing at all,
 * silently, which is exactly how it presents.
 */
export function zoomBy(viewer: Viewer, factor: number, duration = 0.3): void {
  const camera = viewer.camera
  const controller = viewer.scene.screenSpaceCameraController
  const { point, range } = viewCentre(viewer)
  const next = CesiumMath.clamp(
    range * factor,
    controller.minimumZoomDistance ?? 40,
    controller.maximumZoomDistance ?? 40_000,
  )
  placeCamera(viewer, point, camera.heading, camera.pitch, next, duration, sandboxPivot ? sandboxInsetLeft : 0)
}

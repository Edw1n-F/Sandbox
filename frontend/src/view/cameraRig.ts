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
 * ## Framing and navigation are different problems
 *
 * `frameExtent` (the opening view, reset, top-down) *places* the camera: it computes one
 * absolute destination from a heading/pitch/range triple and flies there, deriving its
 * orientation from the destination it just computed.
 *
 * `rotateBy` / `tiltBy` / `zoomBy` place nothing. Each *displaces* the live camera along
 * exactly one axis, and reads no other axis to do it. That separation is the whole point:
 * an earlier version rebuilt the pose from `camera.heading` and `camera.pitch` together on
 * every move, which coupled them. Near the pitch limit the heading is ill-conditioned --
 * `getHeading` is the angle of `cross(direction, up)`, whose length vanishes as the view
 * approaches nadir -- so tilting past the end spun the view instead of stopping. Deriving
 * each axis as a delta on the live camera, and never re-deriving the other two from
 * read-back angles, is what keeps rotate, tilt and zoom from bleeding into each other.
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
  viewer.camera.lookAtTransform(Matrix4.IDENTITY)
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
  // `enableTilt` is left alone here and turned off in `bindOrbitGestures`, which is
  // where Cesium's own tilt is actually replaced. Setting it true here and false there
  // would be a contradiction read in order.
}

/**
 * Rebind the mouse/trackpad so the gestures people reach for do what they expect, and
 * so each one moves exactly one axis.
 *
 * | gesture          | stock behaviour | after this |
 * |------------------|-----------------|------------|
 * | left-drag        | pan + spin      | pan + spin (move the slab) |
 * | right-drag       | zoom            | **tilt (vertical) + rotate (horizontal)** |
 * | middle-drag      | tilt            | tilt (vertical) + rotate (horizontal) |
 * | mouse wheel      | zoom            | zoom |
 * | trackpad 2-finger | zoom           | **tilt (vertical) + rotate (horizontal)** |
 * | ctrl+scroll      | zoom            | zoom |
 * | touch pinch      | zoom            | zoom |
 *
 * ## Telling a mouse wheel from a trackpad two-finger drag
 *
 * They are the same DOM event, which is the whole difficulty. A mouse wheel is a
 * *discrete notch*: no horizontal component, and a large `deltaY` (100 px in Chrome,
 * 120 in Firefox, or `deltaMode` 1 with a handful of lines). A trackpad two-finger drag
 * is a *stream*: many small fractional deltas, roughly 20–60 a second, and never an
 * exact zero on the axis the user did not mean. `classifyWheel` separates them on
 * those three signals, and this handler routes a notch to zoom and a stream to
 * tilt/rotate.
 *
 * ## Why the drags are handled here and not by Cesium
 *
 * Cesium's `tilt3D` ends in `rotate3D`, which is one drag producing *both* a heading
 * change and a pitch change from the same mouse delta. That is actually what this
 * gesture wants -- but it is entangled with Cesium's own pitch clamping and camera
 * frame, and the heading it derives is ill-conditioned near nadir. Handling the drag
 * here keeps each axis a separate `tiltBy`/`rotateBy`, so the axes stay independent
 * and the pitch limit holds. Measured on a stock right-drag: 120 px sideways turned
 * the heading 307 deg, and 120 px down tilted 42 deg.
 */
export function bindOrbitGestures(viewer: Viewer): void {
  const controller = viewer.scene.screenSpaceCameraController
  controller.rotateEventTypes = [CameraEventType.LEFT_DRAG]
  // Nothing tilts through Cesium: the drag is ours, so each axis stays separate.
  // `undefined` rather than `[]` -- `reactToInput` treats both as "no events", but
  // undefined is the documented "disabled" value.
  controller.tiltEventTypes = undefined
  controller.zoomEventTypes = [
    CameraEventType.PINCH,
    {
      eventType: CameraEventType.WHEEL,
      modifier: KeyboardEventModifier.CTRL,
    },
  ]
  // PINCH stays on zoom. It is the touch-screen two-finger pinch and arrives as touch
  // events, not as the `wheel` events the handler below consumes, so the two do not
  // compete: a trackpad swipe tilts and rotates, a touchscreen pinch zooms.
  controller.zoomFactor = 12
  // Tilt and rotate come from our own one-axis handler, so Cesium's own tilt must not
  // also fire on the drags we are using.
  controller.enableTilt = false

  const canvas = viewer.scene.canvas
  if (!canvas) return

  // Wheel: a discrete notch zooms, a stream tilts and rotates. ctrl+wheel is left for
  // Cesium's binding above (the early return lets it through), because a browser sends
  // ctrl+wheel for its own pinch-zoom and that convention is worth keeping. Every move
  // is applied outright, so a stream of events cannot queue anything up.
  let lastWheelAt = 0
  canvas.addEventListener(
    'wheel',
    (event: WheelEvent) => {
      if (event.ctrlKey) return
      event.preventDefault()
      // A timestamp of 0, or one that does not advance, means the gap is unknown;
      // treat that as "not a stream" so a single synthetic event still zooms.
      const gap =
        lastWheelAt > 0 && event.timeStamp > lastWheelAt
          ? event.timeStamp - lastWheelAt
          : Infinity
      lastWheelAt = event.timeStamp
      if (classifyWheel(event.deltaX, event.deltaY, event.deltaMode, gap) === 'zoom') {
        zoomBy(viewer, wheelToZoom(event.deltaY, event.deltaMode))
        return
      }
      const { tiltDeg, rotateDeg } = wheelToOrbit(event.deltaX, event.deltaY, event.deltaMode)
      if (tiltDeg !== 0) tiltBy(viewer, tiltDeg)
      if (rotateDeg !== 0) rotateBy(viewer, rotateDeg)
    },
    { passive: false },
  )

  // Right-drag and middle-drag tilt and rotate: horizontal travel turns the view,
  // vertical travel tilts it, and the two are separate calls so they cannot disturb
  // one another. A `pointerdown` with button 1 (middle) or 2 (right) starts it;
  // `pointerup`/`pointercancel`/`lostpointercapture` end it, so a drag that leaves the
  // canvas cannot leave the binding stuck on.
  let dragButton: number | null = null
  let lastX = 0
  let lastY = 0
  const endDrag = () => {
    dragButton = null
  }
  canvas.addEventListener('pointerdown', (event: PointerEvent) => {
    if (event.button !== 1 && event.button !== 2) return
    dragButton = event.button
    lastX = event.clientX
    lastY = event.clientY
    event.preventDefault()
    canvas.setPointerCapture?.(event.pointerId)
  })
  canvas.addEventListener('pointermove', (event: PointerEvent) => {
    if (dragButton === null) return
    const dx = event.clientX - lastX
    const dy = event.clientY - lastY
    lastX = event.clientX
    lastY = event.clientY
    // Dragging right turns the view the way the arrow keys do; dragging down looks
    // further down, which is a *lower* pitch. Both match the direction Cesium's own
    // drags had, measured: 120 px down moved pitch -33 -> -75.
    if (dx !== 0) rotateBy(viewer, dx * DRAG_ROTATE_DEG_PER_PX)
    if (dy !== 0) tiltBy(viewer, -dy * DRAG_TILT_DEG_PER_PX)
  })
  canvas.addEventListener('pointerup', endDrag)
  canvas.addEventListener('pointercancel', endDrag)
  canvas.addEventListener('lostpointercapture', endDrag)
  // A right-drag otherwise opens the browser context menu on release, which on this
  // canvas means the drag ends with a menu over the map.
  canvas.addEventListener('contextmenu', (event: Event) => event.preventDefault())
}

/** Degrees per CSS pixel of a tilt/rotate drag. Slower than the wheel, because a drag
 *  reports every pixel of travel rather than one delta per notch. */
const DRAG_TILT_DEG_PER_PX = 0.35
const DRAG_ROTATE_DEG_PER_PX = 0.3

/**
 * Degrees of camera motion per CSS pixel of wheel delta or drag.
 *
 * A mouse wheel sends one event per notch (~100 px), so `TILT_DEG_PER_PX` 0.05 makes one
 * notch about the 5 deg the old fixed-step handler used. A trackpad streams hundreds of
 * tiny events, and they add up to something proportionate to the distance the fingers
 * actually travelled. `ROTATE_DEG_PER_PX` is higher because a horizontal swipe covers
 * fewer pixels for the same felt effort.
 */
const TILT_DEG_PER_PX = 0.05
const ROTATE_DEG_PER_PX = 0.12
/** The most one *wheel event* may move the camera. A fast flick can report several
 *  hundred px in a single event; unbounded, that is a lurch. A drag is not capped --
 *  it is a continuous gesture the user is watching, so the cap would only make a fast
 *  drag feel unresponsive. */
const MAX_STEP_DEG = 8
/** A cross-axis delta this small is the fingers drifting, not a decision.
 *  Without it a purely vertical swipe also spins the view. */
const AXIS_DEAD_ZONE_PX = 2
/** `WheelEvent.deltaMode`: 0 pixels, 1 lines, 2 pages. */
const LINE_PX = 16
const PAGE_PX = 400

/**
 * How a wheel event is told apart from a trackpad two-finger drag.
 *
 * A mouse wheel is one discrete notch: a big `deltaY` (100 px in Chrome, 120 in
 * Firefox), no horizontal component at all, and the next notch at least
 * `MOUSE_NOTCH_GAP_MS` away. A trackpad streams events far faster than that, reports
 * fractional deltas, and almost always leaves a stray non-zero `deltaX`.
 *
 * The gap is the load-bearing signal. A mouse reports an exact `deltaX === 0`, which
 * would be conclusive on its own -- but a trackpad can too, on a perfectly vertical
 * swipe, and Chrome's own trackpad handling reports `deltaX` exactly 0 in some
 * configurations. So the gap carries the decision and the other two only corroborate:
 * a fast, small-delta stream is a trackpad whatever its `deltaX` says.
 */
const MOUSE_NOTCH_GAP_MS = 120
const TRACKPAD_DELTA_MAX_PX = 30

export type WheelIntent = 'zoom' | 'orbit'

/**
 * Decide whether one wheel event means "zoom" (mouse wheel) or "tilt/rotate"
 * (trackpad two-finger drag).
 *
 * Pure and exported so the three signals can be tested rather than felt. The
 * alternative -- giving the trackpad its own gesture and leaving the wheel unbound --
 * was the previous state and it was wrong: a mouse wheel then tilted, which is not
 * what a mouse wheel does anywhere else.
 */
export function classifyWheel(
  deltaX: number,
  deltaY: number,
  deltaMode = 0,
  gapMs = Infinity,
): WheelIntent {
  const scale = deltaMode === 1 ? LINE_PX : deltaMode === 2 ? PAGE_PX : 1
  const dy = Math.abs(deltaY * scale)
  const dx = Math.abs(deltaX * scale)
  // A big vertical delta, arriving on its own, is a notch. Everything else is a stream.
  const looksLikeNotch = dy >= TRACKPAD_DELTA_MAX_PX && dx === 0
  return looksLikeNotch && gapMs >= MOUSE_NOTCH_GAP_MS ? 'zoom' : 'orbit'
}

/** Zoom factor per mouse-wheel notch. A notch is ~100 px, so this is a fixed step per
 *  notch rather than a distance-proportional one -- the whole point of a notched wheel
 *  is that every notch is the same size. */
const ZOOM_PER_NOTCH = 1 / 0.8

/**
 * Turn a mouse-wheel notch into a zoom factor, `<1` for zoom in.
 *
 * Discrete by design: the sign of `deltaY` picks the direction and the magnitude is
 * ignored, because a notched wheel's magnitude is an artefact of the browser (100 vs
 * 120) rather than something the user did. Scaling by it would make the same gesture
 * zoom further in Firefox than in Chrome.
 */
export function wheelToZoom(deltaY: number, deltaMode = 0): number {
  const scale = deltaMode === 1 ? LINE_PX : deltaMode === 2 ? PAGE_PX : 1
  const dy = deltaY * scale
  if (dy === 0) return 1
  return dy > 0 ? ZOOM_PER_NOTCH : 1 / ZOOM_PER_NOTCH
}

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

/**
 * Apply a camera move. `duration` parameters were removed from the callers: every move
 * is applied at once.
 *
 * The 0.3 s glide the buttons used to have was cosmetic, and it was the thing that broke.
 * It advanced on `requestAnimationFrame`, and in the headless software renderer this
 * project's browser suites run in, rAF fires roughly once every two seconds -- so a
 * "0.3 s" flight got a single tick, and a press arriving before the next frame got none
 * at all and appeared to do nothing. That is the freeze recorded as STATUS item 15,
 * where a second quick press on a zoom or tilt button left the camera at the first step;
 * the earlier attempts blamed `camera.flyTo`'s flight state, but the frame rate is the
 * common cause. A move that is applied outright cannot be starved of frames, and two
 * presses add up exactly because each applies its whole delta to the live camera.
 *
 * The keyboard and wheel paths have always been instant for the same reason.
 */
function applyImmediately(step: () => void, viewer: Viewer): void {
  step()
  viewer.scene.requestRender()
}

/**
 * A frame centred on the point the camera orbits, with +Z along the local vertical.
 *
 * Rotating inside this frame and restoring `Matrix4.IDENTITY` afterwards is what makes
 * rotate and tilt single-axis. `Camera.rotate(axis, a)` rotates the camera's position,
 * direction and up together about `axis`; pick the local vertical and only the heading
 * can move, pick the (horizontal) right vector and only the pitch can move. Neither
 * reads the other, so neither can disturb the other -- including at the pitch limits,
 * where re-deriving a heading from `cross(direction, up)` is numerically hopeless.
 */
function pivotFrame(pivot: Cartesian3): Matrix4 {
  return Transforms.eastNorthUpToFixedFrame(pivot, Ellipsoid.WGS84)
}

/** Apply `fn` with the camera's frame temporarily set to the pivot's ENU frame. */
function inPivotFrame(viewer: Viewer, pivot: Cartesian3, fn: () => void): void {
  const camera = viewer.camera
  camera.lookAtTransform(pivotFrame(pivot))
  try {
    fn()
  } finally {
    // In a `finally` on purpose. Leaving the camera in a pivot frame would make every
    // later `setView`/`flyTo` destination be read as pivot-relative, so the camera would
    // land somewhere unrelated and every subsequent move would compound the error.
    camera.lookAtTransform(Matrix4.IDENTITY)
  }
}

/** The pitch orbit limits, in radians, as the constants above define them. */
const MIN_PITCH_RAD = CesiumMath.toRadians(MIN_PITCH_DEG)
const MAX_PITCH_RAD = CesiumMath.toRadians(MAX_PITCH_DEG)

/**
 * How far `tiltBy` may turn for a requested `deltaRad`, given the pitch it starts from.
 *
 * Pure, and separate from the camera so it can be tested: the whole reported bug is in
 * this number. Without the clamp a tilt past the limit keeps going, crosses straight
 * down, and comes back up on the other side with the heading flipped ~180 deg -- which
 * is what "it starts rotating instead" is. Clamping the delta to reach the limit exactly
 * and no further means the view simply stops.
 *
 * The sign check covers a case the clamp alone gets backwards. The top-down framing puts
 * the pitch at -90, past the -88 tilt limit, and clamping `-90 + -10` lands on -88 --
 * which, measured from -90, is a move *upwards* of 2 deg in response to a request to tilt
 * *down*. Returning 0 instead leaves the view where it is.
 */
export function pitchDelta(currentPitch: number, deltaRad: number): number {
  const allowed =
    CesiumMath.clamp(currentPitch + deltaRad, MIN_PITCH_RAD, MAX_PITCH_RAD) - currentPitch
  return Math.sign(allowed) === Math.sign(deltaRad) ? allowed : 0
}

/**
 * Turn the camera about the pivot's vertical axis. Heading only; pitch and range are
 * untouched by construction.
 */
export function rotateBy(viewer: Viewer, headingDeg: number): void {
  const pivot = pivotPoint(viewer)
  if (!pivot) return
  const angle = CesiumMath.toRadians(headingDeg)
  applyImmediately(() => {
    inPivotFrame(viewer, pivot, () => viewer.camera.rotate(Cartesian3.UNIT_Z, angle))
  }, viewer)
}

/**
 * Tilt the camera up or down about its own horizontal axis. Pitch only.
 *
 * `camera.right` is horizontal by construction (it is `cross(direction, up)`), so
 * rotating about it changes the pitch and cannot change the heading. At a pitch limit
 * `pitchDelta` returns 0 and this is a no-op, which is the point: the view stops dead
 * rather than sliding into a rotation.
 *
 * The angle is *negated* into `rotate`. `pitchDeg` is in the app's convention, where
 * positive raises the view towards the horizon; `rotate` measures the opposite way, so
 * `+10` on this axis lowers the pitch (measured: -33 -> -43). Getting this backwards is
 * not a small error: the pitch then walks out of its range instead of stopping at the
 * limit, crosses straight down, and the heading flips 180 deg -- which is exactly the
 * "tilting past the limit starts rotating" report, reintroduced.
 */
export function tiltBy(viewer: Viewer, pitchDeg: number): void {
  const pivot = pivotPoint(viewer)
  if (!pivot) return
  const camera = viewer.camera
  const allowed = pitchDelta(camera.pitch, CesiumMath.toRadians(pitchDeg))
  if (allowed === 0) return
  applyImmediately(() => {
    inPivotFrame(viewer, pivot, () => camera.rotate(camera.right, -allowed))
  }, viewer)
}

/**
 * The point the camera turns and dollies about, as close to what the user is looking at
 * as we can get.
 *
 * The sandbox pivot when the terrain tileset has supplied one -- that is the centre of
 * the model, and orbiting it is what makes the district turn in place rather than swing
 * out of frame. A picked ground point under the middle of the visible strip otherwise,
 * so a demo with no tileset still behaves.
 */
function pivotPoint(viewer: Viewer): Cartesian3 | null {
  if (sandboxPivot) return sandboxPivot
  return groundPoint(viewer)
}

/**
 * The point under the middle of the strip of canvas the left panel leaves free.
 *
 * Picking order matters. `scene.pickPosition` reads the depth buffer, so it lands on the
 * actual 3D tile surface -- but it returns a point in absolute world space only when the
 * camera transform is identity, which it is here. `globe.pick` next, for terrain. The
 * ellipsoid last: it is always defined, so a move never fails halfway, and its error is
 * bounded by the relief. Looking at the horizon there is no ground at all, so a point
 * along the view ray stands in and the move still works.
 */
function groundPoint(viewer: Viewer): Cartesian3 | null {
  const camera = viewer.camera
  const scene = viewer.scene
  const canvas = scene.canvas
  const width = Math.max(1, canvas?.clientWidth ?? 1)
  const height = Math.max(1, canvas?.clientHeight ?? 1)
  const inset = Math.min(sandboxPivot ? sandboxInsetLeft : 0, width * 0.4)
  const pixel = new Cartesian2(width / 2 + inset / 2, height / 2)

  const depth = scene.pickPositionSupported ? scene.pickPosition(pixel) : undefined
  if (depth) return depth
  const ray = camera.getPickRay(pixel)
  if (ray) {
    const onGlobe = scene.globe?.pick(ray, scene)
    if (onGlobe) return onGlobe
  }
  const onEllipsoid = camera.pickEllipsoid(pixel, Ellipsoid.WGS84)
  if (onEllipsoid) return onEllipsoid
  if (ray) {
    return Cartesian3.add(
      camera.positionWC,
      Cartesian3.multiplyByScalar(ray.direction, 4000, new Cartesian3()),
      new Cartesian3(),
    )
  }
  return null
}

/**
 * Move towards (<1) or away from (>1) the point under the middle of the user's view.
 *
 * The camera travels along the line joining it to that anchor, so the anchor is exactly
 * the fixed point of the projection and stays put on screen: measured, the anchor moves
 * 0.0 px through a 0.55 zoom. Orientation is read once and never rewritten, so a zoom
 * cannot alter the heading or the pitch, and the distance is clamped to the controller's
 * own limits so it cannot be dolly-walked through the ground.
 *
 * This is deliberately NOT the old behaviour of re-placing the camera about the scene
 * pivot. That re-centred every zoom on the middle of the district, so after a pan a
 * single click pulled the view 464 m back to where the user was not looking.
 */
export function zoomBy(viewer: Viewer, factor: number): void {
  const camera = viewer.camera
  const controller = viewer.scene.screenSpaceCameraController
  const anchor = pivotOrGround(viewer)
  if (!anchor) return
  const offset = Cartesian3.subtract(camera.positionWC, anchor, new Cartesian3())
  const distance = Math.max(Cartesian3.magnitude(offset), 1)
  const next = CesiumMath.clamp(
    distance * factor,
    controller.minimumZoomDistance ?? 40,
    controller.maximumZoomDistance ?? 40_000,
  )
  const unit = Cartesian3.normalize(offset, new Cartesian3())
  // Orientation is untouched -- read it and hand it straight back, so a dolly cannot
  // move the heading or the pitch.
  const direction = Cartesian3.clone(camera.directionWC)
  const up = Cartesian3.clone(camera.upWC)
  applyImmediately(() => {
    camera.setView({
      destination: Cartesian3.add(
        anchor,
        Cartesian3.multiplyByScalar(unit, next, new Cartesian3()),
        new Cartesian3(),
      ),
      orientation: { direction, up },
      convert: false,
    })
  }, viewer)
}

/**
 * `groundPoint`, but falling back to the sandbox pivot when there is no canvas geometry
 * under the middle of the view -- a dolly needs *some* anchor, and the model centre is a
 * far better one than the screen centre.
 */
function pivotOrGround(viewer: Viewer): Cartesian3 | null {
  return groundPoint(viewer) ?? sandboxPivot
}

/**
 * Orbit the camera about what it is looking at. Heading and pitch, as two independent
 * moves: `headingDeg` turns, `pitchDeg` tilts, and neither can move the other.
 */
export function orbitBy(viewer: Viewer, headingDeg: number, pitchDeg: number): void {
  if (headingDeg !== 0) rotateBy(viewer, headingDeg)
  if (pitchDeg !== 0) tiltBy(viewer, pitchDeg)
}


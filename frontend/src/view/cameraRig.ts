/**
 * Camera framing and navigation for a 3D district.
 *
 * ## The bug this file exists to fix
 *
 * `camera.flyTo({ destination: rectangle })` frames a `Rectangle` by putting the
 * camera directly above its centre, looking straight down. Cesium offers no
 * alternative: a rectangle carries no orientation, so the fit is always nadir. For a
 * flood-exposure raster that is the right answer. For a city model it is the wrong
 * one -- pitch -90 deg over 34 m envelopes on 139 m of relief renders a skyline as
 * 2,639 grey squares, which is exactly how the sandbox read before this file existed:
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

/** Metres per degree of latitude. The same figure `cityModel.ts` rotates in. */
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
  const target = Cartesian3.fromDegrees(
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
 * Rebind the mouse so the gestures people reach for do what they expect.
 *
 * ## What Cesium actually does, measured on this viewer
 *
 * | gesture       | stock behaviour | after this |
 * |---------------|-----------------|------------|
 * | left-drag     | pan + spin      | pan + spin |
 * | right-drag    | **zoom**        | orbit      |
 * | middle-drag   | orbit           | tilt       |
 * | Ctrl + drag   | orbit           | tilt       |
 * | Shift + drag  | look            | look       |
 * | wheel / pinch | zoom            | zoom       |
 *
 * Rotation was therefore available but hidden behind middle-drag and Ctrl+drag, while
 * the gesture almost everyone tries first -- right-drag, to turn the model -- silently
 * zoomed instead.
 *
 * ## Why right-drag is *added* to the rotate list rather than moved onto it
 *
 * Because in Cesium's 3D mode there is no separate translate handler at all:
 * `ScreenSpaceCameraController.update3D` reacts to rotate, zoom, tilt and look, and
 * panning is something `spin3D` does as part of rotating. So `rotateEventTypes` is not
 * "the rotate gesture", it is *the drag gesture*, and left-drag stops panning the
 * moment it is taken off that list -- measured, not assumed: reassigning it to
 * right-drag alone left the district completely immovable under left-drag.
 */
export function bindOrbitGestures(viewer: Viewer): void {
  const controller = viewer.scene.screenSpaceCameraController
  controller.rotateEventTypes = [
    CameraEventType.LEFT_DRAG,
    CameraEventType.RIGHT_DRAG,
  ]
  // RIGHT_DRAG is removed from the zoom list, which is the whole point: left to stock it
  // would zoom *and* orbit at once, so the camera would dive while it turned. Measured:
  // the same gesture moved the camera from 3,761 m to 7,471 m of altitude while the
  // heading barely changed.
  //
  // Plain WHEEL is removed too, at the user's request: on a trackpad the two-finger
  // swipe is the gesture people expect to move the map, and since it cannot be panned
  // reliably here (a 2 km cursor-anchored pan arrived as 39 m -- see the note in
  // `CameraController`) it now does nothing at all rather than zooming unexpectedly.
  // Pinch and Ctrl/Cmd+scroll still zoom: browsers deliver a trackpad pinch *as* a
  // Ctrl+wheel event, so one binding covers both.
  controller.zoomEventTypes = [
    CameraEventType.PINCH,
    { eventType: CameraEventType.WHEEL, modifier: KeyboardEventModifier.CTRL },
  ]
  // Faster zoom for what remains. Cesium's default is 5, and the district is 3 km
  // across, so at the stock rate a pinch took several flicks to change scale usefully.
  controller.zoomFactor = 12
  controller.tiltEventTypes = [
    CameraEventType.MIDDLE_DRAG,
    CameraEventType.PINCH,
    { eventType: CameraEventType.LEFT_DRAG, modifier: KeyboardEventModifier.CTRL },
  ]

  // Swallow the plain-wheel events that no longer zoom, so the page cannot scroll
  // behind the canvas and leave the viewer offset from the window. Attached to the
  // canvas, so it dies with the viewer and needs no teardown.
  //
  // The trade-off is stated rather than hidden: because a two-finger swipe and a mouse
  // wheel are the same DOM event, disabling the trackpad gesture necessarily disables
  // wheel zoom for a mouse too. Zoom stays reachable by pinch, Cmd/Ctrl+scroll, the
  // +/− buttons and the +/− keys.
  viewer.scene.canvas?.addEventListener(
    'wheel',
    (event: WheelEvent) => {
      if (event.ctrlKey) return
      event.preventDefault()
    },
    { passive: false },
  )
}

/** The point the camera is looking at, as close to the frame centre as we can get. */
function viewCentre(viewer: Viewer): { point: Cartesian3; range: number } {
  const camera = viewer.camera
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
  placeCamera(viewer, point, heading, pitch, range, duration)
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
  placeCamera(viewer, point, camera.heading, camera.pitch, next, duration)
}

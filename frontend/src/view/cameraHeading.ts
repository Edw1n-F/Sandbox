/**
 * The camera's heading, published for the compass.
 *
 * A tiny external store rather than Zustand state because the heading changes on
 * every frame of a drag and nothing but the compass reads it: routing 60 updates a
 * second through the scene store would re-render subscribers that never look at it.
 * `useSyncExternalStore` re-renders only the component that asks.
 *
 * Lives in `view/` because the Cesium camera is its only writer, and is read by a
 * component through the hook below -- the same direction the other view-to-chrome
 * hooks (`cameraControl`, the overlay status pills) already run.
 */
import { useSyncExternalStore } from 'react'

let headingDeg = 0
const listeners = new Set<() => void>()

/** Normalise any angle into [0, 360). */
export function normaliseHeading(deg: number): number {
  return ((deg % 360) + 360) % 360
}

/**
 * Smallest signed rotation to reach north: positive means the view is rotated
 * clockwise of north, so turning anticlockwise by this much faces north.
 */
export function offNorth(deg: number): number {
  const n = normaliseHeading(deg)
  return n > 180 ? n - 360 : n
}

/** Whether the view is close enough to north-up that a compass adds nothing. */
export function isNorthUp(deg: number, toleranceDeg = 2): boolean {
  return Math.abs(offNorth(deg)) <= toleranceDeg
}

export function publishHeading(deg: number): void {
  const next = normaliseHeading(deg)
  // Whole-degree resolution: a compass needle does not need the decimals, and
  // skipping sub-degree changes stops a slow drag from notifying every frame.
  if (Math.round(next) === Math.round(headingDeg)) return
  headingDeg = next
  listeners.forEach((notify) => notify())
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify)
  return () => listeners.delete(notify)
}

/** The camera heading in degrees clockwise from north, [0, 360). */
export function useCameraHeading(): number {
  return useSyncExternalStore(subscribe, () => headingDeg, () => 0)
}

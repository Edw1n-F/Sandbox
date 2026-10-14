/**
 * The trackpad sensitivity bug, pinned -- and the mouse-wheel/trackpad split.
 *
 * The wheel handler used to apply `Math.sign(delta) * 5 deg` on every event. A
 * mouse wheel sends one event per notch, so that was fine. A trackpad streams
 * ~60 events a second plus a momentum tail after the fingers lift, so one gentle
 * swipe became dozens of 5 deg steps and slammed into the pitch clamp; and since
 * a trackpad never reports an exact zero on the axis you did not mean, a purely
 * vertical swipe also rotated the view.
 *
 * A mouse wheel and a trackpad two-finger drag are the same DOM event, so the two
 * gestures cannot be distinguished by which event arrives -- only by its shape.
 * `classifyWheel` reads that shape, and the tests below pin both halves: a notch
 * still zooms, a stream still tilts and rotates.
 *
 * These tests exist because sensitivity is the kind of bug that only a person
 * notices -- nothing throws, nothing renders wrong -- so the numbers are pinned
 * where a regression will be caught rather than felt.
 */
import { describe, expect, it } from 'vitest'

import { classifyWheel, wheelToOrbit, wheelToZoom } from './cameraRig'

/** Total camera motion from a stream of events, as the handler would apply it. */
function total(events: Array<[number, number]>): { tilt: number; rotate: number } {
  let tilt = 0
  let rotate = 0
  for (const [dx, dy] of events) {
    const o = wheelToOrbit(dx, dy)
    tilt += o.tiltDeg
    rotate += o.rotateDeg
  }
  return { tilt, rotate }
}

describe('classifyWheel', () => {
  it('reads a mouse-wheel notch as a zoom', () => {
    // Chrome's notch is 100 px, Firefox's 120, both with deltaX exactly 0, arriving
    // well over 120 ms after the last one.
    expect(classifyWheel(0, 100, 0, 400)).toBe('zoom')
    expect(classifyWheel(0, 120, 0, 900)).toBe('zoom')
  })

  it('reads a trackpad stream as an orbit, even when it looks like a notch', () => {
    // The load-bearing case. A trackpad can report deltaX exactly 0 on a perfectly
    // vertical swipe, so shape alone is not enough -- the ~16 ms gap is what decides.
    expect(classifyWheel(0, 100, 0, 16)).toBe('orbit')
    expect(classifyWheel(0, 120, 0, 8)).toBe('orbit')
  })

  it('reads small fractional deltas as a trackpad whatever the gap', () => {
    expect(classifyWheel(0, 3, 0, 500)).toBe('orbit')
    expect(classifyWheel(0.4, 12, 0, 500)).toBe('orbit')
  })

  it('reads a horizontal component as a trackpad', () => {
    // A wheel has no horizontal axis; anything with one is a two-finger drag.
    expect(classifyWheel(40, 100, 0, 500)).toBe('orbit')
  })

  it('treats an unknown gap as a notch, so one synthetic event still zooms', () => {
    // A dispatched event can carry a zero timestamp. Reading that as a trackpad would
    // swallow single programmatic wheel events, which the browser suite relies on.
    expect(classifyWheel(0, 100, 0, Infinity)).toBe('zoom')
  })

  it('reads a line-mode notch as a zoom', () => {
    // Firefox and some mice report deltaMode 1; 3 lines is 48 px, over the threshold.
    expect(classifyWheel(0, 3, 1, 500)).toBe('zoom')
  })
})

describe('wheelToZoom', () => {
  it('zooms in on scroll-up and out on scroll-down, by the same factor', () => {
    // Positive deltaY is scroll-down, which zooms out, matching every other map.
    const outFactor = wheelToZoom(100)
    const inFactor = wheelToZoom(-100)
    expect(inFactor).toBeLessThan(1)
    expect(outFactor).toBeGreaterThan(1)
    expect(inFactor * outFactor).toBeCloseTo(1, 6)
  })

  it('is the same step per notch regardless of the browser magnitude', () => {
    // Chrome says 100, Firefox says 120. The same gesture must zoom the same amount,
    // which is why the magnitude is ignored and only the sign is read.
    expect(wheelToZoom(100)).toBe(wheelToZoom(120))
  })

  it('is a no-op for a zero delta', () => {
    expect(wheelToZoom(0)).toBe(1)
  })
})

describe('wheelToOrbit', () => {
  it('moves in proportion to how far the fingers travelled', () => {
    const small = wheelToOrbit(0, 10)
    const large = wheelToOrbit(0, 40)
    expect(Math.abs(large.tiltDeg)).toBeGreaterThan(Math.abs(small.tiltDeg))
  })

  it('a gentle trackpad swipe no longer reaches the pitch clamp', () => {
    // 30 events of 3 px each: about 90 px of finger travel. The old handler gave
    // 30 * 5 = 150 deg, far past the 80 deg usable range.
    const swipe = Array.from({ length: 30 }, () => [0, 3] as [number, number])
    expect(Math.abs(total(swipe).tilt)).toBeLessThan(10)
  })

  it('one mouse-wheel notch is still a clear, usable step', () => {
    // Still a sizeable single step for the trackpad case that reaches here. It is the
    // wheel's *zoom* that is now the notch gesture, not its tilt.
    const notch = wheelToOrbit(0, 100)
    expect(Math.abs(notch.tiltDeg)).toBeGreaterThan(3)
    expect(Math.abs(notch.tiltDeg)).toBeLessThanOrEqual(8)
  })

  it('a vertical swipe does not also rotate the view', () => {
    // Real trackpads report a stray deltaX like 0.4 alongside a deliberate deltaY.
    const o = wheelToOrbit(0.4, 30)
    expect(o.rotateDeg).toBe(0)
    expect(o.tiltDeg).not.toBe(0)
  })

  it('a clearly diagonal drag still does both', () => {
    const o = wheelToOrbit(30, 30)
    expect(o.rotateDeg).not.toBe(0)
    expect(o.tiltDeg).not.toBe(0)
  })

  it('a horizontal swipe rotates and does not tilt', () => {
    const o = wheelToOrbit(40, 1)
    expect(o.rotateDeg).not.toBe(0)
    expect(o.tiltDeg).toBe(0)
  })

  it('ignores sub-pixel jitter entirely', () => {
    expect(wheelToOrbit(0.5, 0.5)).toEqual({ tiltDeg: 0, rotateDeg: 0 })
  })

  it('caps a single violent flick so it cannot lurch the camera', () => {
    const o = wheelToOrbit(0, 2000)
    expect(Math.abs(o.tiltDeg)).toBeLessThanOrEqual(8)
  })

  it('reverses direction with the sign of the delta', () => {
    expect(wheelToOrbit(0, 30).tiltDeg).toBeLessThan(0)
    expect(wheelToOrbit(0, -30).tiltDeg).toBeGreaterThan(0)
    expect(wheelToOrbit(30, 0).rotateDeg).toBeGreaterThan(0)
    expect(wheelToOrbit(-30, 0).rotateDeg).toBeLessThan(0)
  })

  it('reads line-mode wheels (some mice, Firefox) as the same physical distance', () => {
    // deltaMode 1 reports lines, not pixels; without scaling a notch of "3 lines"
    // would read as 3 px and do nothing.
    const lines = wheelToOrbit(0, 3, 1)
    expect(Math.abs(lines.tiltDeg)).toBeGreaterThan(1)
  })
})

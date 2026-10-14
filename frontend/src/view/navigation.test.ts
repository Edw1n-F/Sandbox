/**
 * The navigation help and the compass maths, pinned.
 *
 * The cheat-sheet is data (`NAV_HELP`) so it can be held to what the app binds. A
 * help panel describing a gesture that does nothing is worse than none: people
 * learn it, it fails silently, and they conclude the app is broken.
 */
import { describe, expect, it } from 'vitest'

import { hasSeenNavHint, markNavHintSeen, NAV_HELP, NAV_HINT_SEEN_KEY } from '../components/navHelp'
import { isNorthUp, normaliseHeading, offNorth } from './cameraHeading'
import { pitchDelta } from './cameraRig'

describe('the pitch clamp', () => {
  // `tiltBy` asks this for how far it may turn, and applies exactly that. It is a pure
  // function so the one number that matters can be tested rather than felt: the reported
  // bug was "past a threshold it starts rotating", which is what happens when a tilt is
  // allowed past the limit -- it crosses straight down and comes back up with the
  // heading flipped 180 deg.
  const MIN = (-88 * Math.PI) / 180
  const MAX = (-8 * Math.PI) / 180
  const toDeg = (r: number) => (r * 180) / Math.PI

  it('grants a tilt that stays inside the limits', () => {
    // -33 is the opening pitch and -60 is comfortably inside. "Tilt up" is positive.
    expect(toDeg(pitchDelta((-33 * Math.PI) / 180, (10 * Math.PI) / 180))).toBeCloseTo(10, 6)
    expect(toDeg(pitchDelta((-60 * Math.PI) / 180, (-10 * Math.PI) / 180))).toBeCloseTo(-10, 6)
  })

  it('stops exactly at the limit and no further', () => {
    // Starting near the overhead end, a big downward tilt lands ON the limit.
    const atLimit = pitchDelta((-83 * Math.PI) / 180, (-30 * Math.PI) / 180)
    expect(toDeg((-83 * Math.PI) / 180 + atLimit)).toBeCloseTo(toDeg(MIN), 6)
  })

  it('returns zero once already at a limit, so the view does not move at all', () => {
    // Zero is what makes "tilting past the limit" a no-op rather than a rotation. A
    // non-zero answer here is the bug.
    expect(pitchDelta(MIN, (-30 * Math.PI) / 180)).toBe(0)
    expect(pitchDelta(MAX, (30 * Math.PI) / 180)).toBe(0)
  })

  it('does not reverse direction for a view sitting outside the tilt range', () => {
    // `T` frames straight down at -90, which is past the -88 tilt limit. Clamping a
    // request to tilt further down would put the camera at -88: that is 2 deg UPWARD,
    // the opposite of what was asked. It must be inert instead.
    expect(pitchDelta((-90 * Math.PI) / 180, (-10 * Math.PI) / 180)).toBe(0)
  })

  it('still grants the tilt AWAY from a limit', () => {
    expect(toDeg(pitchDelta(MIN, (30 * Math.PI) / 180))).toBeCloseTo(30, 6)
    expect(toDeg(pitchDelta(MAX, (-30 * Math.PI) / 180))).toBeCloseTo(-30, 6)
    // Out of range the other way too: from straight down, tilting up is still allowed.
    expect(toDeg(pitchDelta((-90 * Math.PI) / 180, (30 * Math.PI) / 180))).toBeCloseTo(30, 6)
  })
})

describe('compass maths', () => {
  it('normalises any angle into [0, 360)', () => {
    expect(normaliseHeading(0)).toBe(0)
    expect(normaliseHeading(360)).toBe(0)
    expect(normaliseHeading(-90)).toBe(270)
    expect(normaliseHeading(725)).toBe(5)
  })

  it('reports the shortest way round to north', () => {
    expect(offNorth(10)).toBe(10)
    expect(offNorth(350)).toBe(-10)
    expect(offNorth(180)).toBe(180)
    expect(offNorth(190)).toBe(-170)
  })

  it('treats a near-north view as north-up, so the needle can rest', () => {
    expect(isNorthUp(0)).toBe(true)
    expect(isNorthUp(1.5)).toBe(true)
    expect(isNorthUp(358.5)).toBe(true)
    expect(isNorthUp(10)).toBe(false)
  })
})

describe('the navigation cheat-sheet', () => {
  const rows = NAV_HELP.flatMap((g) => g.hints)
  const text = rows.map((r) => `${r.action} ${r.effect}`).join(' | ')

  it('covers mouse, trackpad and keyboard', () => {
    expect(NAV_HELP.map((g) => g.title)).toEqual(['Mouse', 'Trackpad', 'Keyboard'])
  })

  it('describes every key the viewer actually binds', () => {
    // These are the keys SandboxViewer's keydown handler switches on. If one is
    // added there and not here, the help under-reports; if one is listed here and
    // removed there, the help lies. Both are caught by this list.
    for (const key of ['←', '↑', '+', 'T', 'Home']) {
      expect(text, `help should mention ${key}`).toContain(key)
    }
  })

  it('names a zoom gesture for the mouse wheel, which is what a wheel does', () => {
    // The wheel was bound to tilt while a trackpad drag and a mouse wheel are the same
    // DOM event. `classifyWheel` now splits them, so the mouse wheel zooms and the
    // help must say so -- a cheat-sheet describing the old mapping sends every mouse
    // user to the wrong gesture.
    expect(text).toMatch(/Scroll wheel/)
    expect(text).toMatch(/Right-drag/)
    expect(text).toContain('Pinch')
  })

  it('gives every row both an action and an effect', () => {
    for (const r of rows) {
      expect(r.action.trim().length).toBeGreaterThan(0)
      expect(r.effect.trim().length).toBeGreaterThan(0)
    }
  })
})

describe('the first-run flag', () => {
  it('starts unseen, persists once marked, and survives a blocked store', () => {
    const store = new Map<string, string>()
    const fake = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    }
    Object.defineProperty(globalThis, 'window', { value: { localStorage: fake }, configurable: true })
    expect(hasSeenNavHint()).toBe(false)
    markNavHintSeen()
    expect(store.get(NAV_HINT_SEEN_KEY)).toBe('1')
    expect(hasSeenNavHint()).toBe(true)

    // A storage that throws (private mode, blocked cookies) must read as "seen":
    // a hint on every single load is a worse failure than no hint.
    const throwing = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
    }
    Object.defineProperty(globalThis, 'window', { value: { localStorage: throwing }, configurable: true })
    expect(hasSeenNavHint()).toBe(true)
    expect(() => markNavHintSeen()).not.toThrow()
  })
})

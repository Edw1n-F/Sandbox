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

  it('does not promise a plain scroll wheel zooms (it tilts)', () => {
    // The wheel is bound to tilt; only ctrl+scroll and pinch zoom. Saying
    // "scroll to zoom" would send every mouse user to the wrong gesture.
    expect(text).not.toMatch(/(^|\| )Scroll\b/)
    expect(text).toContain('Ctrl + scroll')
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

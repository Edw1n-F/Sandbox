/**
 * What the navigation help says, as data.
 *
 * Kept apart from the component so a test can hold it to the gestures that
 * `cameraRig.bindOrbitGestures` and the keyboard handler in `SandboxViewer`
 * actually bind. A cheat-sheet that describes a gesture the app does not have is
 * worse than no cheat-sheet: it teaches people to do something that silently does
 * nothing, and they conclude the app is broken.
 *
 * The wheel is described as it behaves TODAY: a mouse wheel notch zooms, and a trackpad
 * two-finger drag tilts and rotates -- the same DOM event, told apart by `classifyWheel`.
 * If that mapping changes, this table is the second place to update, and
 * `navigation.test.ts` is what will say so.
 */

export interface NavHint {
  /** What the user does. */
  action: string
  /** What happens. */
  effect: string
}

export interface NavGroup {
  title: string
  hints: NavHint[]
}

export const NAV_HELP: NavGroup[] = [
  {
    title: 'Mouse',
    hints: [
      { action: 'Left-drag', effect: 'Move the view across the district' },
      { action: 'Scroll wheel', effect: 'Zoom in and out' },
      { action: 'Right-drag', effect: 'Up and down tilts, left and right rotates' },
    ],
  },
  {
    title: 'Trackpad',
    hints: [
      { action: 'One-finger drag', effect: 'Move the view across the district' },
      { action: 'Two-finger drag', effect: 'Up and down tilts, left and right rotates' },
      { action: 'Pinch', effect: 'Zoom in and out' },
    ],
  },
  {
    title: 'Keyboard',
    hints: [
      { action: '← →', effect: 'Rotate the view' },
      { action: '↑ ↓', effect: 'Tilt up and down' },
      { action: '+  −', effect: 'Zoom in and out' },
      { action: 'T', effect: 'Look straight down' },
      { action: 'Home or 0', effect: 'Reset the view' },
    ],
  },
]

/** Where the user has already been told how to navigate. */
export const NAV_HINT_SEEN_KEY = 'sandbox.navHintSeen'

/** Read the flag without letting a blocked or full storage take the page down. */
export function hasSeenNavHint(): boolean {
  try {
    return window.localStorage.getItem(NAV_HINT_SEEN_KEY) === '1'
  } catch {
    // Storage can throw in private mode or when blocked. Treat that as "seen":
    // showing a hint on every load would be a worse failure than showing none.
    return true
  }
}

export function markNavHintSeen(): void {
  try {
    window.localStorage.setItem(NAV_HINT_SEEN_KEY, '1')
  } catch {
    // Nothing to do: the hint simply shows again next visit.
  }
}

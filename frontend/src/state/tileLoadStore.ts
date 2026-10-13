/**
 * Load state of the self-hosted 3D Tiles tilesets (buildings and terrain).
 *
 * Module state rather than component state because the tilesets are created inside
 * the Cesium effect in `SandboxViewer`, and the loading screen is a sibling component
 * that only needs to read "is anything still coming in".
 */
import { create } from 'zustand'

export type TilesetName = 'buildings' | 'terrain'
export type TilesetStatus = 'loading' | 'ready' | 'failed'

interface TileLoadState {
  status: Partial<Record<TilesetName, TilesetStatus>>
  /** Tile requests still in flight, summed over tilesets, for the progress text. */
  pending: Partial<Record<TilesetName, number>>
  set: (name: TilesetName, status: TilesetStatus) => void
  setPending: (name: TilesetName, pending: number) => void
  reset: (name: TilesetName) => void
}

export const useTileLoadStore = create<TileLoadState>((set) => ({
  status: {},
  pending: {},
  set: (name, status) => set((s) => ({ status: { ...s.status, [name]: status } })),
  setPending: (name, pending) => set((s) => ({ pending: { ...s.pending, [name]: pending } })),
  reset: (name) =>
    set((s) => {
      const status = { ...s.status }
      const pending = { ...s.pending }
      delete status[name]
      delete pending[name]
      return { status, pending }
    }),
}))

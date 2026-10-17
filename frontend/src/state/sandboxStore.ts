/**
 * The sandbox: the interventions the user has placed (Stage 4).
 *
 * Server-side this is one plan per scene (the staged plan, `pins.py`). This
 * store holds its id and its pinned rows, so the Interventions panel can list
 * them, the viewer can draw them and the run can score them -- one read, three
 * consumers. It is kept apart from `planStore` (which owns plans and the
 * project selection) because the two answer different questions: which
 * projects are toggled on, and what the user has added on top.
 *
 * Every write goes through the API first and then `refresh`, so the panel,
 * the viewer and the run cannot disagree about what is on the sandbox.
 */
import { create } from 'zustand'

import { errorMessage } from '@/api/client'
import { fetchStagedPins, type StagedFeature } from '@/api/pinsApi'
import { repinFeature, unpinFeature } from '@/api/sandboxApi'

export interface SandboxState {
  /** The staged plan's id, or null before anything has been placed. */
  stagedPlanId: string | null
  /** The pinned rows only: an unpinned row is off the sandbox. */
  features: StagedFeature[]
  error: string | null

  refresh: (sceneId: string) => Promise<void>
  /** Take one intervention off the sandbox (unpin), then re-read it. */
  remove: (sceneId: string, featureId: string) => Promise<void>
  /**
   * The last undoable action, or null.
   *
   * Only removals are undoable, and only the most recent one: an unpin leaves
   * the row in the staged plan with its id and provenance intact, so putting it
   * back is a flag flip. An *addition* has no inverse of that kind -- pinning a
   * removed row is one call, while un-pinning an added one is a different
   * guarantee (the row was never there before), and offering "undo" for it
   * would overstate what the store can promise.
   */
  undoable: { featureId: string; label: string } | null
  undo: (sceneId: string) => Promise<void>
  dismissUndo: () => void
}

export const useSandboxStore = create<SandboxState>((set, get) => ({
  stagedPlanId: null,
  features: [],
  error: null,
  undoable: null,

  refresh: async (sceneId) => {
    try {
      const staged = await fetchStagedPins(sceneId)
      set({
        stagedPlanId: staged.planId,
        features: staged.features.filter((f) => f.pinned),
        error: null,
      })
    } catch (err) {
      set({ error: errorMessage(err) })
    }
  },

  remove: async (sceneId, featureId) => {
    // Remember what was removed BEFORE the write, so a failed request cannot
    // leave an "Undo" button for something still on the sandbox.
    const gone = get().features.find((f) => f.id === featureId)
    try {
      await unpinFeature(sceneId, featureId)
    } catch (err) {
      set({ error: errorMessage(err) })
      return
    }
    set({
      undoable: gone
        ? { featureId, label: gone.interventionType.replace(/_/g, ' ') }
        : null,
    })
    await get().refresh(sceneId)
  },

  undo: async (sceneId) => {
    const pending = get().undoable
    if (!pending) return
    try {
      await repinFeature(sceneId, pending.featureId)
    } catch (err) {
      set({ error: errorMessage(err) })
      return
    }
    set({ undoable: null })
    await get().refresh(sceneId)
  },

  dismissUndo: () => set({ undoable: null }),
}))

/**
 * The plan ids a run scores: the toggled projects plus the sandbox.
 *
 * The staged plan is added only while something is pinned on it, so an empty
 * sandbox leaves "no projects selected" meaning exactly that -- a baseline run.
 */
export function runPlanIds(selectedPlanIds: string[], sandbox: SandboxState): string[] {
  const ids = [...selectedPlanIds]
  if (sandbox.stagedPlanId && sandbox.features.length > 0 && !ids.includes(sandbox.stagedPlanId)) {
    ids.push(sandbox.stagedPlanId)
  }
  return ids
}

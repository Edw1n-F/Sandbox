/**
 * Plan + intervention state.
 *
 * The baseline plan is selected on load because the Oct 6 deliverable is
 * "existing region, nothing on top" — no planned projects are applied.
 */
import { create } from 'zustand'

import { listInterventions, listPlans } from '@/api/planApi'
import { errorMessage } from '@/api/client'
import type { FeatureCollection, Plan } from '@/api/types'

export interface PlanState {
  plans: Plan[]
  activePlanId: string | null
  interventions: FeatureCollection
  loading: boolean
  error: string | null

  load: (sceneId: string) => Promise<void>
  selectPlan: (planId: string) => Promise<void>
  clearError: () => void
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

export const usePlanStore = create<PlanState>((set, get) => ({
  plans: [],
  activePlanId: null,
  interventions: EMPTY,
  loading: false,
  error: null,

  load: async (sceneId) => {
    set({ loading: true, error: null })
    try {
      const plans = await listPlans(sceneId)
      set({ plans })

      // Prefer the baseline plan; fall back to whatever exists so an empty or
      // oddly-typed store still renders something.
      const preferred =
        plans.find((p) => p.planType === 'baseline') ?? plans[0] ?? null
      if (preferred) {
        await get().selectPlan(preferred.planId)
      } else {
        set({ activePlanId: null, interventions: EMPTY, loading: false })
      }
    } catch (error) {
      set({ error: errorMessage(error), loading: false })
    }
  },

  selectPlan: async (planId) => {
    set({ loading: true, error: null, activePlanId: planId })
    try {
      const interventions = await listInterventions(planId)
      set({ interventions, loading: false })
    } catch (error) {
      // A failed要素 load must not wipe the plan list; the viewer still has a
      // valid scene to draw.
      set({ interventions: EMPTY, error: errorMessage(error), loading: false })
    }
  },

  clearError: () => set({ error: null }),
}))

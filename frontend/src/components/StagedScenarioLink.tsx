/**
 * Wires the staged scenario into the run -- the loop's final beat.
 *
 * `SuggestionsPanel` pins features into the staged plan, but the pin's truth
 * lives server-side: the panel keeps no reference to the staged plan id, and
 * the run (`SlidersPanel` -> `sceneStore.run`) composes exactly what
 * `planStore.selectedPlanIds` holds. Without a bridge between the two, a pin
 * would render a green "pinned" badge and then score nothing on the next run --
 * the loop's last hop dropped.
 *
 * The panel cannot be edited (the UI agent owns it) and there is no pin event
 * to subscribe to, so this component reads the staged scenario from
 * `GET /api/scenes/{sceneId}/pins` and reconciles `selectedPlanIds` against it:
 * a staged plan with pinned rows is added to the run selection when it is not
 * there, and removed again when every row is unpinned (the backend already
 * drops `pinned: false` rows server-side -- this keeps the panel's count
 * honest). It renders nothing itself.
 *
 * The staged plan id is appended, never prepended: `ProjectCatalog` points the
 * viewer at the *last* selected plan when the selection changes, and appending
 * keeps a catalog toggle the viewer's current subject.
 */
import { useEffect } from 'react'

import { fetchStagedPins } from '@/api/pinsApi'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

/** How often the staged scenario is reconciled, ms. */
const SYNC_INTERVAL_MS = 5000

export default function StagedScenarioLink() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const selectedPlanIds = usePlanStore((s) => s.selectedPlanIds)
  const setSelectedPlanIds = usePlanStore((s) => s.setSelectedPlanIds)

  useEffect(() => {
    if (!sceneId) return
    let live = true

    const sync = async () => {
      try {
        const staged = await fetchStagedPins(sceneId)
        if (!live || !staged.planId) return
        const hasPinned = staged.features.some((f) => f.pinned)
        const selected = selectedPlanIds.includes(staged.planId)
        if (hasPinned && !selected) {
          setSelectedPlanIds([...selectedPlanIds, staged.planId])
        } else if (!hasPinned && selected) {
          // Nothing pinned under this plan -- take it back out of the run so
          // the panel's selection matches what the run would compose.
          setSelectedPlanIds(
            selectedPlanIds.filter((id) => id !== staged.planId),
          )
        }
      } catch {
        // The staged scenario is optional to running. A failed read here (no
        // session, a 404 mid-load) must not surface in the HUD or disturb the
        // selection already on screen; the next tick retries.
      }
    }

    void sync()
    const timer = window.setInterval(() => void sync(), SYNC_INTERVAL_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [sceneId, selectedPlanIds, setSelectedPlanIds])

  return null
}

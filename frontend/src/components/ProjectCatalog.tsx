/**
 * Top-right "Projects" panel: the declared, fixed-footprint projects.
 *
 * Each row is a planned project that exists as real geometry -- a plan seeded
 * from `backend/data/projects/<id>.json` by `scripts/seed_projects.py`, with a
 * GLB model and a footprint of its own. Toggling a row adds its plan id to
 * `selectedPlanIds`, and the next run scores every selected project **together**.
 *
 * ## Cumulative selection
 *
 * The brief's problem statement is explicitly about the combined effect
 * ("rather than only assessing each project individually"), so this panel
 * multi-selects. The selected plans' intervention features are merged
 * server-side into one scenario by `intervention_features_many`, and the backend
 * refuses the run if the reworked land passes 40% of the study area.
 *
 * `activePlanId` (which plan the 3D view highlights) and `selectedPlanIds`
 * (which plans the run scores) are deliberately separate store fields: with
 * cumulative projects they genuinely differ. A project row therefore does NOT
 * call `selectPlan`/`resyncViewer` -- a project's plan can carry a tank polygon,
 * and highlighting it would draw that polygon as a translucent slab over the
 * model it describes.
 *
 * ## Rows come from the plans, not from a list in this file
 *
 * The seeder tags every project's plan with `params.projectId`, so the rows are
 * simply the scene's plans that carry one. That is the whole reason adding a
 * project needs no change here -- and it picks up effect-only projects with no
 * 3D model, which a list built from the model URLs would miss. The plan id
 * itself is a uuid minted at seed time, so the project id is the stable handle.
 *
 * ## The generated catalog was removed
 *
 * Until 16 Oct 2026 this panel also rendered a *derived* catalog: four
 * placeholder projects whose parcels were computed from land cover and the wet
 * grid by `scripts/make_project_catalog.py` and served at
 * `GET /scenes/{id}/project-catalog`. They are gone -- HSK Park and the MTR
 * station are declared projects with real models, and a derived parcel beside
 * them read as a plan that exists. The suggester still derives candidate sites
 * (`app/services/siting.py`, `GET /scenes/{id}/suggestions`); that is a
 * different step, and it stays.
 */
import Box from '@mui/material/Box'
import CircularProgress from '@mui/material/CircularProgress'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import type { Plan } from '@/api/types'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

function FixedProjectRows() {
  const plans = usePlanStore((s) => s.plans)
  const projects = plans.filter(
    (p) => (p.params as { projectId?: string } | undefined)?.projectId,
  )
  if (projects.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        No fixed-footprint projects seeded on this scene. Run backend/scripts/seed_projects.py.
      </Typography>
    )
  }
  return (
    <>
      {projects.map((plan) => (
        <FixedProjectRow key={plan.planId} plan={plan} />
      ))}
    </>
  )
}

function FixedProjectRow({ plan }: { plan: Plan }) {
  const selectedPlanIds = usePlanStore((s) => s.selectedPlanIds)
  const setSelectedPlanIds = usePlanStore((s) => s.setSelectedPlanIds)
  const planProjectLoading = useSceneStore((s) => s.planProjectLoading)

  const on = selectedPlanIds.includes(plan.planId)
  const params = (plan.params ?? {}) as { note?: string; model?: string }

  const toggle = () => {
    if (planProjectLoading) return
    setSelectedPlanIds(
      on
        ? selectedPlanIds.filter((id) => id !== plan.planId)
        : [...selectedPlanIds, plan.planId],
    )
  }

  return (
    <Box sx={{ mb: 1, border: '1px solid', borderColor: on ? 'rgba(92,255,157,0.5)' : 'divider', borderRadius: 2, overflow: 'hidden' }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        onClick={toggle}
        sx={{
          px: 1.25, py: 0.75,
          cursor: planProjectLoading ? 'wait' : 'pointer',
          bgcolor: on ? 'rgba(92,255,157,0.12)' : 'transparent',
          '&:hover': { bgcolor: on ? 'rgba(92,255,157,0.18)' : 'rgba(92,255,157,0.06)' },
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography className="hud-mono" variant="body2" sx={{ fontWeight: 600, fontSize: 12.5 }}>
            {plan.label}
          </Typography>
          {on && (
            <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: '#5cff9d', boxShadow: '0 0 6px #5cff9d' }} />
          )}
        </Stack>
        {planProjectLoading
          ? <CircularProgress size={10} sx={{ color: '#5cff9d' }} />
          : <SwitchPill on={on} />}
      </Stack>
      <Box sx={{ px: 1.25, pb: 0.9, pt: 0.1 }}>
        <Typography variant="caption" color="text.secondary" display="block">
          {params.note ?? 'Fixed footprint, scored in runs. Not a generated proposal.'}
        </Typography>
      </Box>
    </Box>
  )
}

/** The little on/off pill. */
function SwitchPill({ on }: { on: boolean }) {
  return (
    <Box
      sx={{
        width: 14,
        height: 8,
        borderRadius: 8,
        bgcolor: on ? '#5cff9d' : 'rgba(160,190,175,0.25)',
        position: 'relative',
        transition: 'all 0.2s',
      }}
    >
      <Box
        sx={{
          position: 'absolute',
          top: 1,
          width: 6,
          height: 6,
          borderRadius: '50%',
          bgcolor: on ? '#04120a' : '#dce8e2',
          left: on ? 7 : 1,
          transition: 'left 0.2s',
        }}
      />
    </Box>
  )
}

export default function ProjectCatalog() {
  const selectedCount = usePlanStore((s) => s.selectedPlanIds.length)

  return (
    <HudPanel
      title="Projects"
      right={
        selectedCount > 0 ? (
          <Typography className="hud-mono" sx={{ fontSize: 9, color: '#5cff9d' }}>
            {selectedCount} active
          </Typography>
        ) : undefined
      }
      sx={{ width: '100%' }}
    >
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        Toggle projects to score them <strong>together</strong>. Runs combine every
        active project, up to 40% of the study area.
      </Typography>
      <FixedProjectRows />
    </HudPanel>
  )
}

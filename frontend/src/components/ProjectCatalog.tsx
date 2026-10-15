/**
 * Top-right project catalog.
 *
 * A small, predefined set of the "buildings the planner can place in the
 * sandbox" — schools, facilities, a filled pond — each with two phase variants.
 * Toggling a phase generates a plan via the existing AI pipeline; every toggled
 * phase stays selected, and the next run scores them **together**.
 *
 * ## Cumulative selection
 *
 * The brief's problem statement is explicitly about the combined effect
 * ("rather than only assessing each project individually"), so this panel
 * multi-selects. The selected plans' intervention features are merged server-side
 * into one scenario by `intervention_features_many`, and the backend refuses the
 * run if the reworked land passes 40% of the study area — five legal 24%
 * interventions must not compose into a district-wide land-use swap.
 *
 * `activePlanId` (which plan the 3D view highlights) and `selectedPlanIds`
 * (which plans the run scores) are deliberately separate store fields: with
 * cumulative projects they genuinely differ.
 *
 * ## What's still a placeholder
 *
 * The footprints are templated natural-language instructions sent to `ai_plan`,
 * not surveyed project data, so placement is re-derived on every toggle rather
 * than read from a fixed footprint. That is STATUS gap 2 and it is flagged in
 * the panel rather than hidden.
 */
import Box from '@mui/material/Box'
import CircularProgress from '@mui/material/CircularProgress'
import Collapse from '@mui/material/Collapse'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import { useCallback, useState } from 'react'

import HudPanel from '@/components/HudPanel'
import { createAiPlan } from '@/api/planApi'
import type { Plan } from '@/api/types'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

interface Phase {
  id: string
  label: string
  instruction: string
}
interface Project {
  id: string
  name: string
  note: string
  phases: Phase[]
}

/** The placeholder catalog. Swap for real HSK project data when it lands. */
const PROJECTS: Project[] = [
  {
    id: 'school',
    name: 'School',
    note: 'Paved campus + footprint → more runoff.',
    phases: [
      { id: 'p1', label: 'Phase 1 — core', instruction: 'place a small school facility in the northeast of the study area' },
      { id: 'p2', label: 'Phase 2 — expanded', instruction: 'place a large expanded school campus in the northeast of the study area' },
    ],
  },
  {
    id: 'pond',
    name: 'Pond infill',
    note: 'Filling a pond removes storage → worse flooding.',
    phases: [
      { id: 'full', label: 'Full infill', instruction: 'fill the pond in the south of the study area, removing the water body' },
      { id: 'partial', label: 'Partial infill', instruction: 'partially fill the pond in the south of the study area' },
    ],
  },
  {
    id: 'facility',
    name: 'Public facility',
    note: 'Built footprint on currently open land.',
    phases: [
      { id: 'small', label: 'Small facility', instruction: 'place a small public facility in the centre of the study area' },
      { id: 'large', label: 'Large facility', instruction: 'place a large public facility complex in the centre of the study area' },
    ],
  },
  {
    id: 'green',
    name: 'Green corridor',
    note: 'Trees + vegetation → lower heat, more retention.',
    phases: [
      { id: 'road', label: 'Along main road', instruction: 'plant a green tree corridor along the main road across the study area' },
      { id: 'river', label: 'Along riverfront', instruction: 'plant a green tree corridor along the riverfront of the study area' },
    ],
  },
]

/**
 * A planned project that exists as real geometry rather than as a generated
 * proposal -- the MTR station and its GLB today, and any project a teammate adds
 * as a `backend/data/projects/<id>.json` tomorrow.
 *
 * It joins the run the same way a catalog phase does -- by its plan id going into
 * `selectedPlanIds` -- so its effects are scored and the "Run N projects" count
 * includes it. What it deliberately does NOT do is call `selectPlan`/
 * `resyncViewer` the way a catalog phase does: a project's plan can carry a tank
 * polygon, and selecting the plan for the viewer would draw that polygon as a
 * translucent slab over the model it describes.
 *
 * ## Rows come from the plans, not from a list in this file
 *
 * The seeder tags every project's plan with `params.projectId`, so the rows are
 * simply the scene's plans that carry one. That is the whole reason adding a
 * project needs no change here -- and it picks up effect-only projects with no
 * 3D model, which a list built from the model URLs would miss. The plan id itself
 * is a uuid minted at seed time, so the project id is the stable handle.
 */
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

/** The little on/off pill. One definition, used by both row kinds. */
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
  const sceneId = useSceneStore((s) => s.sceneId)
  const selectPlan = usePlanStore((s) => s.selectPlan)
  const refreshPlans = usePlanStore((s) => s.refreshPlans)
  const setSelectedPlanIds = usePlanStore((s) => s.setSelectedPlanIds)
  const selectedCount = usePlanStore((s) => s.selectedPlanIds.length)

  // key ("<project>:<phase>") -> the plan id that toggle produced. Keeping the
  // plan id rather than a boolean means toggling off does not have to re-derive
  // anything, and the run can send exactly the plans on screen.
  const [active, setActive] = useState<Record<string, string>>({})
  const [expanded, setExpanded] = useState<string | null>(PROJECTS[0]?.id ?? null)
  const [busy, setBusy] = useState(false)

  /** Point the 3D view at the newest selection, or back to today when empty. */
  const resyncViewer = useCallback(
    async (planIds: string[]) => {
      if (!sceneId) return
      if (planIds.length) {
        await selectPlan(planIds[planIds.length - 1])
        return
      }
      // Nothing selected -> show today. The store picks the plan whose type is
      // 'baseline' on load, so re-fetching and picking the first baseline
      // restores "today" without a dedicated clear endpoint.
      await refreshPlans(sceneId)
      const plans = usePlanStore.getState().plans
      const baseline = plans.find((p) => p.planType === 'baseline')
      if (baseline) await selectPlan(baseline.planId)
    },
    [sceneId, selectPlan, refreshPlans],
  )

  const toggle = async (project: Project, phase: Phase) => {
    if (!sceneId || busy) return
    const key = `${project.id}:${phase.id}`

    // Toggling an active phase off: drop it, and re-run with the rest.
    if (key in active) {
      const next = { ...active }
      delete next[key]
      setActive(next)
      const planIds = Object.values(next)
      setSelectedPlanIds(planIds)
      setBusy(true)
      try {
        await resyncViewer(planIds)
      } finally {
        setBusy(false)
      }
      return
    }

    setBusy(true)
    try {
      const r = await createAiPlan(sceneId, phase.instruction)
      if (r.planId) {
        const next = { ...active, [key]: r.planId }
        setActive(next)
        const planIds = Object.values(next)
        setSelectedPlanIds(planIds)
        await refreshPlans(sceneId)
        await resyncViewer(planIds)
      }
    } finally {
      setBusy(false)
    }
  }

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
        active project, up to 40% of the study area. Placeholder catalog.
      </Typography>
      <FixedProjectRows />
      {PROJECTS.map((project) => {
        const isOpen = expanded === project.id
        const onPhases = project.phases.filter((p) => `${project.id}:${p.id}` in active)
        return (
          <Box key={project.id} sx={{ mb: 0.5, border: '1px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ px: 1.25, py: 0.75, cursor: 'pointer', '&:hover': { bgcolor: 'rgba(92,255,157,0.06)' } }}
              onClick={() => setExpanded(isOpen ? null : project.id)}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography className="hud-mono" variant="body2" sx={{ fontWeight: 600, fontSize: 12.5 }}>
                  {project.name}
                </Typography>
                {onPhases.length > 0 && (
                  <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: '#5cff9d', boxShadow: '0 0 6px #5cff9d' }} />
                )}
              </Stack>
              <IconButton size="small" sx={{ p: 0, color: 'text.secondary' }} aria-label={isOpen ? 'Collapse' : 'Expand'}>
                <span style={{ fontSize: 10, transition: 'transform 0.2s', display: 'inline-block', transform: isOpen ? 'rotate(90deg)' : 'none' }}>▶</span>
              </IconButton>
            </Stack>
            <Collapse in={isOpen}>
              <Box sx={{ px: 1.25, pb: 1, pt: 0.25 }}>
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.75 }}>
                  {project.note}
                </Typography>
                <Stack spacing={0.5}>
                  {project.phases.map((phase) => {
                    const key = `${project.id}:${phase.id}`
                    const on = key in active
                    return (
                      <Stack
                        key={phase.id}
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        onClick={() => void toggle(project, phase)}
                        sx={{
                          cursor: busy ? 'wait' : 'pointer',
                          px: 1,
                          py: 0.6,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: on ? 'rgba(92,255,157,0.5)' : 'transparent',
                          bgcolor: on ? 'rgba(92,255,157,0.12)' : 'rgba(255,255,255,0.03)',
                          '&:hover': { bgcolor: on ? 'rgba(92,255,157,0.18)' : 'rgba(92,255,157,0.06)' },
                          opacity: busy && !on ? 0.5 : 1,
                        }}
                      >
                        <Typography variant="caption" sx={{ fontSize: 11, color: on ? '#5cff9d' : 'text.primary' }}>
                          {phase.label}
                        </Typography>
                        <SwitchPill on={on} />
                      </Stack>
                    )
                  })}
                </Stack>
              </Box>
            </Collapse>
          </Box>
        )
      })}
    </HudPanel>
  )
}

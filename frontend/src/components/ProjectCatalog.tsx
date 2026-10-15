/**
 * Top-right project catalog.
 *
 * A small, predefined set of the "buildings the planner can place in the
 * sandbox" -- each with two phase variants. Toggling a phase creates a plan
 * from **fixed footprints** read from the backend catalog; every toggled phase
 * stays selected, and the next run scores them **together**.
 *
 * ## Cumulative selection
 *
 * The brief's problem statement is explicitly about the combined effect
 * ("rather than only assessing each project individually"), so this panel
 * multi-selects. The selected plans' intervention features are merged server-side
 * into one scenario by `intervention_features_many`, and the backend refuses the
 * run if the reworked land passes 40% of the study area -- five legal 24%
 * interventions must not compose into a district-wide land-use swap.
 *
 * `activePlanId` (which plan the 3D view highlights) and `selectedPlanIds`
 * (which plans the run scores) are deliberately separate store fields: with
 * cumulative projects they genuinely differ.
 *
 * ## From sentences to footprints
 *
 * This panel used to send a natural-language instruction per phase
 * ("place a large expanded school campus in the northeast of the study area")
 * to the AI planner, which re-derived a footprint on every toggle -- so the same
 * phase toggled twice was two different geometries, and a before/after
 * comparison was not a comparison. The panel said as much in a caption.
 *
 * Now the footprints come from `GET /api/scenes/{sceneId}/project-catalog`,
 * generated once from the rasters by `scripts/make_project_catalog.py`, and the
 * plan is created with that geometry verbatim. The provenance is shown rather
 * than hidden: a footprint is a candidate parcel read from land cover at 5 m,
 * not a surveyed site, and the panel says so.
 */
import Box from '@mui/material/Box'
import CircularProgress from '@mui/material/CircularProgress'
import Collapse from '@mui/material/Collapse'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import { useCallback, useEffect, useState } from 'react'

import HudPanel from '@/components/HudPanel'
import {
  createPlan,
  fetchProjectCatalog,
  type CatalogPhase,
  type CatalogProject,
  type ProjectCatalog,
} from '@/api/catalogApi'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

/** The axis colour, matching the ledger's own legend in `interventionLayer.ts`. */
const AXIS_COLOR: Record<string, string> = {
  drainage: '#1f6feb',
  heat: '#2ea043',
  runoff: '#8957e5',
}

/**
 * A planned project that exists as real geometry rather than as a generated
 * proposal: the MTR station and its GLB.
 *
 * It joins the run the same way a catalog phase does -- by its plan id going into
 * `selectedPlanIds` -- so its storage effect and its massing effect are scored,
 * and the "Run N projects" count includes it. What it deliberately does NOT do is
 * call `selectPlan`/`resyncViewer` the way a catalog phase does: the station's
 * plan carries a tank polygon, and selecting the plan for the viewer would draw
 * that polygon as a translucent slab over the station model it describes.
 *
 * ## Why the plan is looked up rather than hardcoded
 *
 * The plan id is a uuid minted at seed time, so it cannot be a constant here. The
 * seeder tags the plan with `params.projectId`, which is the stable handle; this
 * finds it among the scene's plans.
 */
const STATION_PROJECT_ID = 'mtr-station'

function FixedProjectRow() {
  const plans = usePlanStore((s) => s.plans)
  const selectedPlanIds = usePlanStore((s) => s.selectedPlanIds)
  const setSelectedPlanIds = usePlanStore((s) => s.setSelectedPlanIds)
  const planProjectLoading = useSceneStore((s) => s.planProjectLoading)

  const plan = plans.find((p) => (p.params as { projectId?: string } | undefined)?.projectId === STATION_PROJECT_ID)
  const on = Boolean(plan && selectedPlanIds.includes(plan.planId))

  const toggle = () => {
    if (!plan || planProjectLoading) return
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
          cursor: plan && !planProjectLoading ? 'pointer' : 'not-allowed',
          bgcolor: on ? 'rgba(92,255,157,0.12)' : 'transparent',
          '&:hover': { bgcolor: on ? 'rgba(92,255,157,0.18)' : 'rgba(92,255,157,0.06)' },
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography className="hud-mono" variant="body2" sx={{ fontWeight: 600, fontSize: 12.5 }}>
            HSK MTR station
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
          {plan
            ? 'Fixed footprint, scored in runs: 22.1 m massing and assumed storage. Not a generated proposal.'
            : 'Not seeded on this scene. Run backend/scripts/seed_projects.py.'}
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

  const [catalog, setCatalog] = useState<ProjectCatalog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // key ("<project>:<phase>") -> the plan id that toggle produced. Keeping the
  // plan id rather than a boolean means toggling off does not have to re-derive
  // anything, and the run can send exactly the plans on screen.
  const [active, setActive] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!sceneId) return
    let live = true
    void fetchProjectCatalog(sceneId)
      .then((doc) => {
        if (live) {
          setCatalog(doc)
          setError(null)
          setExpanded(doc.projects[0]?.id ?? null)
        }
      })
      .catch((err: unknown) => {
        // A missing catalog is a 503 with the regeneration command in the
        // detail. Showing that verbatim is more useful than "failed to load".
        if (live) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      live = false
    }
  }, [sceneId])

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

  const toggle = async (project: CatalogProject, phase: CatalogPhase) => {
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
      const created = await createPlan(sceneId, {
        label: `${project.name} — ${phase.label}`,
        source: 'manual',
        params: {
          projectId: project.id,
          phaseId: phase.id,
          catalogDate: project.date,
          catalogConfidence: project.confidence,
        },
        // The geometry is the catalog's own, sent verbatim: no re-derivation,
        // so toggling this phase twice produces the same footprint twice.
        interventions: phase.interventions.map((f) => ({
          geometry: f.geometry,
          interventionType: f.interventionType,
          source: 'manual' as const,
          params: { ...f.params, provenance: f.provenance },
          style: f.style,
        })),
      })
      const next = { ...active, [key]: created.planId }
      setActive(next)
      const planIds = Object.values(next)
      setSelectedPlanIds(planIds)
      await refreshPlans(sceneId)
      await resyncViewer(planIds)
    } finally {
      setBusy(false)
    }
  }

  const totalHa = (phase: CatalogPhase) =>
    phase.interventions.reduce((sum, f) => sum + f.provenance.areaHa, 0)

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
      {/* main's fixed, data-driven project (the MTR station, from
          backend/data/projects/mtr-station.json). It sits above the catalog
          because it is the one project whose footprint and effects are
          declared rather than derived. */}
      <FixedProjectRow />

      {error && (
        <Typography variant="caption" color="error" display="block" sx={{ mb: 1 }}>
          {error}
        </Typography>
      )}

      {!catalog && !error && (
        <Typography variant="caption" color="text.secondary">
          Loading the project catalog…
        </Typography>
      )}

      {catalog?.projects.map((project) => {
        const isOpen = expanded === project.id
        const onPhases = project.phases.filter((p) => `${project.id}:${p.id}` in active)
        return (
          <Box
            key={project.id}
            sx={{
              mb: 0.5,
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{
                px: 1.25,
                py: 0.75,
                cursor: 'pointer',
                '&:hover': { bgcolor: 'rgba(92,255,157,0.06)' },
              }}
              onClick={() => setExpanded(isOpen ? null : project.id)}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography
                  className="hud-mono"
                  variant="body2"
                  sx={{ fontWeight: 600, fontSize: 12.5 }}
                >
                  {project.name}
                </Typography>
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    bgcolor: AXIS_COLOR[project.axis] ?? '#8b949e',
                  }}
                  title={`axis: ${project.axis}`}
                />
                {onPhases.length > 0 && (
                  <Box
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      bgcolor: '#5cff9d',
                      boxShadow: '0 0 6px #5cff9d',
                    }}
                  />
                )}
              </Stack>
              <IconButton
                size="small"
                sx={{ p: 0, color: 'text.secondary' }}
                aria-label={isOpen ? 'Collapse' : 'Expand'}
              >
                <span
                  style={{
                    fontSize: 10,
                    transition: 'transform 0.2s',
                    display: 'inline-block',
                    transform: isOpen ? 'rotate(90deg)' : 'none',
                  }}
                >
                  ▶
                </span>
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
                        title={`${phase.grounding.evidence} — ${phase.grounding.metrics.cells ?? 0} cells`}
                        sx={{
                          cursor: busy ? 'wait' : 'pointer',
                          px: 1,
                          py: 0.6,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: on ? 'rgba(92,255,157,0.5)' : 'transparent',
                          bgcolor: on ? 'rgba(92,255,157,0.12)' : 'rgba(255,255,255,0.03)',
                          '&:hover': {
                            bgcolor: on ? 'rgba(92,255,157,0.18)' : 'rgba(92,255,157,0.06)',
                          },
                          opacity: busy && !on ? 0.5 : 1,
                        }}
                      >
                        <Stack spacing={0.25}>
                          <Typography
                            variant="caption"
                            sx={{ fontSize: 11, color: on ? '#5cff9d' : 'text.primary' }}
                          >
                            {phase.label}
                          </Typography>
                          <Typography
                            className="hud-mono"
                            variant="caption"
                            sx={{ fontSize: 9, color: 'text.secondary' }}
                          >
                            {phase.interventions.length} feature
                            {phase.interventions.length === 1 ? '' : 's'} ·{' '}
                            {totalHa(phase).toFixed(1)} ha
                          </Typography>
                        </Stack>
                        <SwitchPill on={on} />
                      </Stack>
                    )
                  })}
                </Stack>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mt: 0.75, fontSize: 9.5, lineHeight: 1.4 }}
                >
                  {project.confidence} · source: {project.source} · as of {project.date}
                </Typography>
              </Box>
            </Collapse>
          </Box>
        )
      })}

      {catalog && (
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          sx={{ mt: 1, fontSize: 9.5, lineHeight: 1.4 }}
        >
          {catalog.warning}
        </Typography>
      )}
    </HudPanel>
  )
}

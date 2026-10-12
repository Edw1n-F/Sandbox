/**
 * Top-right project catalog (placeholder).
 *
 * A small, predefined set of the "buildings the planner can place in the
 * sandbox" — schools, facilities, a filled pond — each with two phase
 * variants. Toggling a phase generates a plan via the existing AI pipeline and
 * selects it, so the 3D view and the next run reflect the choice.
 *
 * ## What's a placeholder
 *
 * The footprints are templated natural-language instructions sent to `ai_plan`,
 * not surveyed project data. Multi-project accumulation is not supported yet
 * (each toggle replaces the active plan) — that is flagged in the panel rather
 * than hidden, so a planner is not left expecting a composite scenario.
 */
import Box from '@mui/material/Box'
import Collapse from '@mui/material/Collapse'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import { useState } from 'react'

import HudPanel from '@/components/HudPanel'
import { createAiPlan } from '@/api/planApi'
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

export default function ProjectCatalog() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const selectPlan = usePlanStore((s) => s.selectPlan)
  const refreshPlans = usePlanStore((s) => s.refreshPlans)
  // The active project+phase. null = baseline (nothing placed).
  const [active, setActive] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(PROJECTS[0]?.id ?? null)
  const [busy, setBusy] = useState(false)

  const toggle = async (project: Project, phase: Phase) => {
    if (!sceneId || busy) return
    const key = `${project.id}:${phase.id}`
    // Toggling the active phase off → back to baseline (nothing placed).
    if (key === active) {
      setBusy(true)
      try {
        // Select the baseline plan: the store picks the one whose type is
        // 'baseline' on load, so re-fetching and picking the first baseline
        // restores "today" without a dedicated clear endpoint.
        await refreshPlans(sceneId)
        const plans = usePlanStore.getState().plans
        const baseline = plans.find((p) => p.planType === 'baseline')
        if (baseline) await selectPlan(baseline.planId)
        setActive(null)
      } finally {
        setBusy(false)
      }
      return
    }
    setBusy(true)
    try {
      const r = await createAiPlan(sceneId, phase.instruction)
      if (r.planId) {
        await refreshPlans(sceneId)
        await selectPlan(r.planId)
        setActive(key)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <HudPanel title="Projects" sx={{ width: 280 }}>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        Place a proposed project in the sandbox. One active per run (placeholder).
      </Typography>
      {PROJECTS.map((project) => {
        const isOpen = expanded === project.id
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
                <Typography variant="body2" sx={{ fontWeight: 600, fontSize: 12.5 }}>
                  {project.name}
                </Typography>
                {project.phases.some((p) => `${project.id}:${p.id}` === active) && (
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
                    const on = key === active
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

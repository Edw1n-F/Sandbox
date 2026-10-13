/**
 * The provenance receipt on a chat card: which run, on which data, under which
 * assumptions, so a reader can check an answer instead of trusting it.
 *
 * Built from the run result the question was asked about and snapshotted onto the
 * message at ask time. If it were read live from the store, running again would
 * silently change what an earlier card claims to rest on.
 *
 * Every line comes from the run. Nothing here is typed by hand, so the receipt
 * cannot name a dataset the run did not use.
 */
import type { RunResult } from './types'

export interface ReceiptLine {
  label: string
  value: string
}

export interface Receipt {
  /** The one-line form shown while the receipt is collapsed. */
  summary: string
  lines: ReceiptLine[]
}

/** Build a receipt from a run, or null when the question had no run behind it. */
export function buildReceipt(run: RunResult | null | undefined): Receipt | null {
  if (!run) return null

  const planIds = run.inputs.planIds ?? []
  const scenario =
    planIds.length === 0
      ? "Today's land cover (no projects)"
      : `${planIds.length} project${planIds.length === 1 ? '' : 's'} applied`

  const lines: ReceiptLine[] = [
    { label: 'Run', value: run.runId },
    { label: 'Rainfall', value: `${run.inputs.rainfallMm.toFixed(0)} mm total` },
    { label: 'Scenario', value: scenario },
  ]

  const tide = run.stats.tide
  if (tide) lines.push({ label: 'Tide', value: tide.label })

  const temp = run.stats.temperature
  if (temp) {
    lines.push({
      label: 'Surface temperature',
      value: `${temp.scenarioMeanC.toFixed(1)} °C scenario mean`,
    })
  }

  const sources = run.sources
  if (sources) {
    lines.push({ label: 'Terrain', value: sources.terrain })
    lines.push({ label: 'Land cover', value: sources.landCover })
    if (sources.lst) lines.push({ label: 'Heat anchor', value: sources.lst })
  }

  lines.push({ label: 'Status', value: run.status })

  return { summary: `Run ${run.runId.slice(0, 8)} · ${run.inputs.rainfallMm.toFixed(0)} mm`, lines }
}

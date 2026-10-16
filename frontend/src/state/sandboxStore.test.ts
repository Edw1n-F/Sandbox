/**
 * The sandbox's two rules the run depends on.
 *
 * 1. A run scores the toggled projects **plus** what is placed, and an empty
 *    sandbox adds nothing -- so "no projects selected" still means a baseline.
 * 2. The input card sends only what the user typed. A value left to the AI is
 *    omitted, which is how the backend knows to label it an estimate; sending
 *    the estimate itself would record a guess as the user's number.
 */
import { describe, expect, it } from 'vitest'

import type { SandboxInput } from '@/api/sandboxApi'
import { defaultAnswers, fieldError, toValues } from '@/components/inputAnswers'
import { runPlanIds, type SandboxState } from './sandboxStore'

function sandbox(over: Partial<SandboxState>): SandboxState {
  return {
    stagedPlanId: null,
    features: [],
    error: null,
    refresh: async () => {},
    remove: async () => {},
    ...over,
  }
}

const placed = [{
  id: 'f1', interventionType: 'water_reservoir', geometry: { type: 'Polygon', coordinates: [] },
  params: {}, style: {}, pinned: true, provenance: null,
}] as unknown as SandboxState['features']

const DEPTH: SandboxInput = {
  key: 'storageDepthM', label: 'Usable storage depth', unit: 'm',
  min: 0.05, max: 10, estimate: 1.5, estimateBasis: 'class default',
}

describe('runPlanIds', () => {
  it('adds the staged plan when something is placed', () => {
    expect(runPlanIds(['project-park'], sandbox({ stagedPlanId: 'st', features: placed })))
      .toEqual(['project-park', 'st'])
  })

  it('adds nothing for an empty sandbox, so a baseline run stays a baseline', () => {
    expect(runPlanIds([], sandbox({ stagedPlanId: 'st', features: [] }))).toEqual([])
  })

  it('never lists the staged plan twice', () => {
    expect(runPlanIds(['st'], sandbox({ stagedPlanId: 'st', features: placed }))).toEqual(['st'])
  })
})

describe('the input card answers', () => {
  it('starts every field on the AI estimate', () => {
    expect(defaultAnswers([DEPTH])).toEqual({ storageDepthM: { mode: 'ai', text: '1.5' } })
  })

  it('sends nothing for a field left to the AI', () => {
    expect(toValues([DEPTH], defaultAnswers([DEPTH]))).toEqual({})
  })

  it('sends the number the user typed', () => {
    expect(toValues([DEPTH], { storageDepthM: { mode: 'user', text: '3' } }))
      .toEqual({ storageDepthM: 3 })
  })

  it('refuses a depth the run would refuse, before it is sent', () => {
    expect(fieldError(DEPTH, { mode: 'user', text: '40' })).toMatch(/between/)
    expect(fieldError(DEPTH, { mode: 'user', text: '' })).toMatch(/number/)
    expect(fieldError(DEPTH, { mode: 'user', text: '3' })).toBeNull()
    expect(fieldError(DEPTH, { mode: 'ai', text: '' })).toBeNull()
  })
})

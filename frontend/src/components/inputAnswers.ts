/**
 * The input card's answer state, kept apart from `InputCard.tsx` so that file
 * exports only a component (React fast refresh).
 *
 * Each field is either the user's own number or handed to the AI's estimate.
 * Only the user's numbers go on the wire; a key left out is the backend's cue
 * to write the estimate and label it `ai_estimate`.
 */
import type { SandboxInput } from '@/api/sandboxApi'

/** One field's state: the user's own number, or the AI's estimate. */
export interface InputAnswer {
  mode: 'user' | 'ai'
  text: string
}

export type InputAnswers = Record<string, InputAnswer>

/** The starting answers: every field handed to the AI until the user says otherwise. */
export function defaultAnswers(inputs: SandboxInput[]): InputAnswers {
  return Object.fromEntries(
    inputs.map((i) => [i.key, { mode: 'ai' as const, text: String(i.estimate) }]),
  )
}

/** Why a field is not acceptable yet, or null. Mirrors the backend's range check. */
export function fieldError(input: SandboxInput, answer: InputAnswer | undefined): string | null {
  if (!answer || answer.mode === 'ai') return null
  const value = Number(answer.text)
  if (answer.text.trim() === '' || !Number.isFinite(value)) return 'enter a number'
  if (value < input.min || value > input.max) {
    return `between ${input.min} and ${input.max} ${input.unit}`
  }
  return null
}

/** The wire `values` entry: only the fields the user typed. */
export function toValues(inputs: SandboxInput[], answers: InputAnswers): Record<string, number> {
  const out: Record<string, number> = {}
  for (const input of inputs) {
    const answer = answers[input.key]
    if (answer?.mode === 'user') out[input.key] = Number(answer.text)
  }
  return out
}

/**
 * Is every field answered? The one rule the Add and Save buttons share.
 *
 * "Let the AI estimate" is an answer; "Enter my own" with an empty or
 * out-of-range number is not. So the button stays disabled only while a typed
 * value is unusable, never because the planner chose the estimate.
 */
export function allAnswered(inputs: SandboxInput[], answers: InputAnswers | undefined): boolean {
  return inputs.every((input) => fieldError(input, answers?.[input.key]) === null)
}

/** `water_reservoir` -> "Water reservoir": the one name the chat and the panel both use. */
export function interventionName(kind: string): string {
  const words = kind.split('_').join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * The fast-path routing vocabulary, mirroring `contracts/intent_vocabulary.json`.
 *
 * DESIGN §2.5, cause R2: the client's `looksLikeIntervention` used a hand-typed
 * twelve-verb regex that was *smaller* than the server's, so `dig`, `create`,
 * `grow` and `retain` never took the fast path even when a model was live. The
 * fix is not a bigger regex -- it is one vocabulary with two consumers:
 *
 *   contracts/intent_vocabulary.json   the single source (generated from
 *                                      backend/app/agent/intent.py)
 *   this file                          the client's copy, used only to skip the
 *                                      2-5 s SSE round trip when an instruction
 *                                      obviously reads as a proposal
 *
 * The server's `agent/intent.py` is the **authority**: this fast path only
 * decides whether to *try* the gate early, and the `answer` event still routes
 * on its own. `intentVocabulary.test.ts` asserts this file equals the fixture,
 * and `backend/tests/test_intent_routing.py` asserts the fixture equals the
 * backend, so the two vocabularies cannot silently diverge.
 *
 * Nothing here calls a model or a clock: it is a pure string check.
 */

export interface IntentVocabulary {
  version: number
  /** Verbs that read as adding something to the scene. */
  interventionVerbs: string[]
  /** Verbs that read as removing something; the hint is shaped differently. */
  removalVerbs: string[]
  /** Proposal noun phrase -> intervention kind. */
  nouns: Record<string, string>
  /** Intervention kind -> display word. */
  kinds: Record<string, string>
  /** Region code -> the words that read inside a hint. */
  regions: Record<string, string>
}

/**
 * The vocabulary, copied verbatim from `contracts/intent_vocabulary.json`.
 * `intentVocabulary.test.ts` fails if the two ever differ.
 */
export const INTENT_VOCABULARY: IntentVocabulary = {
  version: 1,
  interventionVerbs: [
    'add',
    'place',
    'create',
    'build',
    'dig',
    'plant',
    'grow',
    'fill',
    'retain',
    'retrofit',
    'convert',
    'replace',
    'install',
    'put',
    'increase',
    'expand',
  ],
  removalVerbs: ['remove', 'delete', 'drop', 'take out'],
  nouns: {
    'green space': 'green_space',
    park: 'green_space',
    'urban green': 'green_space',
    'tree planting': 'green_space',
    'permeable paving': 'permeable_paving',
    'permeable pavement': 'permeable_paving',
    'water reservoir': 'water_reservoir',
    'flood storage': 'water_reservoir',
    'retention basin': 'water_reservoir',
    'storage basin': 'water_reservoir',
    drain: 'drainage',
    drainage: 'drainage',
    channel: 'drainage',
    swale: 'drainage',
    road: 'road',
    street: 'road',
    railing: 'road',
    reservoir: 'water_reservoir',
    pond: 'water_reservoir',
    // Synonyms the routing test found missing, mirrored from
    // contracts/intent_vocabulary.json (and therefore from
    // `ai_plan.EN_ALIASES`, the one vocabulary). Without them a proposal-shaped
    // turn like "dig a basin in the north" never took the client fast path.
    'green corridor': 'green_space',
    'tree corridor': 'green_space',
    'permeable plaza': 'permeable_paving',
    basin: 'water_reservoir',
    'floodable park': 'flood_storage',
    wetland: 'flood_storage',
  },
  kinds: {
    water_reservoir: 'water reservoir',
    green_space: 'green space',
    permeable_paving: 'permeable paving',
    flood_storage: 'floodable ground',
    drainage: 'drainage',
    road: 'road',
  },
  regions: {
    north: 'the north',
    south: 'the south',
    east: 'the east',
    west: 'the west',
    centre: 'the centre',
    'north-east': 'the north-east',
    'north-west': 'the north-west',
    'south-east': 'the south-east',
    'south-west': 'the south-west',
    all: 'the whole area',
  },
}

/** Escape the regex metacharacters a phrase could contain. */
function escapeRegExp(phrase: string): string {
  return phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** A whole-word matcher for a phrase, cached so a hot path builds it once. */
const wordReCache = new Map<string, RegExp>()
function wordRe(phrase: string): RegExp {
  let re = wordReCache.get(phrase)
  if (!re) {
    re = new RegExp(`\\b${escapeRegExp(phrase)}\\b`)
    wordReCache.set(phrase, re)
  }
  return re
}

/**
 * Does this read as an intervention instruction rather than a question?
 *
 * The server's `parse_intervention_intent` requires a recognised **verb** *and*
 * a recognised **noun**; this mirrors that so the fast path does not open a gate
 * for "add a bike lane" (a verb, no proposal type). A trailing question mark
 * short-circuits, as it did before. Hyphens are flattened the same way the
 * server does, so "north-west" is one region.
 *
 * A `false` here is not a verdict: the SSE round trip still runs and its
 * `answer` event is the authority.
 */
export function looksLikeIntervention(text: string, vocab: IntentVocabulary): boolean {
  const trimmed = text.trim()
  if (trimmed === '') return false
  if (/\?\s*$/.test(trimmed)) return false

  const lower = trimmed.toLowerCase().replace(/-/g, ' ')
  const hasVerb = [...vocab.interventionVerbs, ...vocab.removalVerbs].some((v) =>
    wordRe(v).test(lower),
  )
  if (!hasVerb) return false
  return Object.keys(vocab.nouns).some((n) => wordRe(n).test(lower))
}

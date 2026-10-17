/**
 * Drift test: the client's fast-path vocabulary must equal the fixture.
 *
 * DESIGN §2.5 pins the two vocabularies together so cause R2 cannot return: the
 * client regex was a second, smaller list than the server's, and nothing failed
 * when they drifted. This test reads `contracts/intent_vocabulary.json` -- the
 * file the backend generates from `agent/intent.py` -- and asserts
 * `INTENT_VOCABULARY` matches it field for field.
 *
 * The backend half of the pin lives in `backend/tests/test_intent_routing.py`
 * (`test_the_fixture_matches_the_backend_vocabulary`), so the chain is closed:
 * intent.py <-> fixture <-> this file.
 */
import { describe, expect, it } from 'vitest'

import { INTENT_VOCABULARY, looksLikeIntervention } from './intentVocabulary'
import fixture from '../../../contracts/intent_vocabulary.json'

describe('intentVocabulary drift', () => {
  it('matches contracts/intent_vocabulary.json exactly', () => {
    expect(INTENT_VOCABULARY.version).toBe(fixture.version)
    expect(INTENT_VOCABULARY.interventionVerbs).toEqual(fixture.interventionVerbs)
    expect(INTENT_VOCABULARY.removalVerbs).toEqual(fixture.removalVerbs)
    expect(INTENT_VOCABULARY.nouns).toEqual(fixture.nouns)
    expect(INTENT_VOCABULARY.kinds).toEqual(fixture.kinds)
    expect(INTENT_VOCABULARY.regions).toEqual(fixture.regions)
  })

  it('carries the verbs the old client regex dropped (R2)', () => {
    for (const verb of ['dig', 'create', 'grow', 'retain']) {
      expect(INTENT_VOCABULARY.interventionVerbs).toContain(verb)
    }
  })
})

describe('looksLikeIntervention', () => {
  it('takes the fast path for the design\'s phrasings, including dig/create/grow', () => {
    for (const phrase of [
      'add a water reservoir in the north',
      'dig a retention basin in the north',
      'create permeable paving across the whole area',
      'grow a green space in the east',
      'retain water in a reservoir in the north',
      'remove the drainage in the north',
    ]) {
      expect(looksLikeIntervention(phrase, INTENT_VOCABULARY)).toBe(true)
    }
  })

  it('leaves questions to the server', () => {
    for (const phrase of [
      'what changed?',
      'why did the buffer overflow?',
      'how much runoff was stored?',
    ]) {
      expect(looksLikeIntervention(phrase, INTENT_VOCABULARY)).toBe(false)
    }
  })

  it('does not open a gate for a verb with no proposal type', () => {
    expect(looksLikeIntervention('add a bike lane', INTENT_VOCABULARY)).toBe(false)
  })

  it('takes the fast path for a plural noun, as the server does', () => {
    // The server's _match_noun accepts `(?:e?s)?`, so "add two water reservoirs"
    // is an intervention; the client fast path must agree, or the turn falls
    // through to the chat round trip and produces a chat card *and* a gate.
    for (const phrase of [
      'add two water reservoirs',
      'dig three retention basins in the north',
      'create permeable pavings',
    ]) {
      expect(looksLikeIntervention(phrase, INTENT_VOCABULARY)).toBe(true)
    }
  })

  it('is not fooled by a substring', () => {
    // "drainage" contains "drain", but neither is a verb here; and "road" inside
    // "broad" must not match on a word boundary.
    expect(looksLikeIntervention('broadly speaking about the run', INTENT_VOCABULARY)).toBe(false)
  })
})

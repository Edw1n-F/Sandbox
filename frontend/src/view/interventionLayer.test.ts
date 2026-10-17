/**
 * `applyStyle`: how a feature's `style` becomes a Cesium material.
 *
 * This is the only part of `interventionLayer.ts` that does not need a live
 * `Viewer`, and it is the part with the silent failures:
 *
 * - An unparseable colour must not empty the layer. `Color.fromCssColorString`
 *   returns `undefined` rather than throwing, and taking that at face value
 *   without guarding meant one bad hex string could take the whole render down.
 * - A polygon with an `extrusionHeight` must **not** also be clamped to ground,
 *   or the two offsets compound and the block floats above the surface.
 * - Assigning a raw `Color` where Cesium expects a `Property` typechecks
 *   loosely and silently does nothing, so the tests assert on the *property
 *   value*, not on the assignment having happened.
 *
 * The rest of the module -- loading a FeatureCollection, adding it to a scene,
 * requesting a frame -- needs a real GL context and is covered by
 * `verify-3d.mjs` against a live render.
 */
import { Color, HeightReference } from 'cesium'
import { describe, expect, it } from 'vitest'

import { applyStyle } from './interventionLayer'

/**
 * The smallest thing `applyStyle` reads.
 *
 * Deliberately a plain object rather than a `GeoJsonDataSource`: the function
 * only touches `entities.values` and each entity's `properties`/`polygon`/
 * `polyline`/`point`, so a stub exercises exactly the contract and does not
 * drag a GL context into a unit suite.
 */
function entityWith(style: unknown) {
  const polygon: Record<string, unknown> = {}
  return {
    polygon,
    polyline: undefined as Record<string, unknown> | undefined,
    point: undefined as Record<string, unknown> | undefined,
    properties: {
      style: { getValue: () => style },
    },
  }
}

function styleInto(entities: unknown[]) {
  const dataSource = { entities: { values: entities } } as never
  applyStyle(dataSource)
  return entities
}

/**
 * Read the colour out of a `ColorMaterialProperty` / `ConstantProperty`.
 *
 * The colour lives on the property's `color` field rather than coming back from
 * `getValue()`, which needs a `JulianDate` this suite has no reason to build.
 */
function colourOf(holder: Record<string, unknown>): Color | undefined {
  // A polygon/polyline carries a `ColorMaterialProperty`, whose `.color` is a
  // `ConstantProperty`; a point carries a `ConstantProperty` directly. Both
  // expose `getValue()`, so one read covers the three geometry kinds.
  const property = (holder.material ?? holder.color) as
    | { color?: { getValue: () => unknown }; getValue?: () => unknown }
    | undefined
  if (!property) return undefined
  const inner = property.color ?? property
  const value = inner.getValue?.()
  return value instanceof Color ? value : undefined
}

describe('applyStyle', () => {
  it('applies a feature colour and its opacity to the polygon', () => {
    const [entity] = styleInto([
      entityWith({ color: '#5cff9d', opacity: 0.4 }),
    ]) as Array<ReturnType<typeof entityWith>>

    const colour = colourOf(entity.polygon)
    expect(colour).toBeDefined()
    expect(colour!.red).toBeCloseTo(0x5c / 255, 3)
    expect(colour!.green).toBeCloseTo(0xff / 255, 3)
    expect(colour!.blue).toBeCloseTo(0x9d / 255, 3)
    // The alpha is the *style's* opacity, not Cesium's default of 1.
    expect(colour!.alpha).toBeCloseTo(0.4, 3)
  })

  it('defaults opacity to opaque when the style omits it', () => {
    const [entity] = styleInto([entityWith({ color: '#ffffff' })]) as Array<
      ReturnType<typeof entityWith>
    >
    expect(colourOf(entity.polygon)!.alpha).toBeCloseTo(1, 3)
  })

  it('ignores an unparseable colour instead of throwing or emptying the layer', () => {
    const entities = styleInto([
      entityWith({ color: 'not-a-colour' }),
      entityWith({ color: '#ff6b6b' }),
    ]) as Array<ReturnType<typeof entityWith>>

    // The bad one is left unstyled, and the good one still styles. A throw here
    // would have taken both -- and the layer -- down.
    expect(colourOf(entities[0].polygon)).toBeUndefined()
    expect(colourOf(entities[1].polygon)).toBeDefined()
  })

  it('a feature with no style is left alone', () => {
    const [entity] = styleInto([entityWith(undefined)]) as Array<
      ReturnType<typeof entityWith>
    >
    expect(entity.polygon.material).toBeUndefined()
    expect(entity.polygon.extrudedHeight).toBeUndefined()
  })

  it('an extruded polygon is NOT clamped to ground, or the block floats', () => {
    const [entity] = styleInto([
      entityWith({ color: '#5cff9d', extrusionHeight: 12 }),
    ]) as Array<ReturnType<typeof entityWith>>

    expect(
      (entity.polygon.extrudedHeight as { getValue: () => number }).getValue(),
    ).toBe(12)
    expect(
      (entity.polygon.heightReference as { getValue: () => unknown }).getValue(),
    ).toBe(HeightReference.NONE)
  })

  it('a polygon with no extrusion stays clamped to the ground', () => {
    const [entity] = styleInto([entityWith({ color: '#5cff9d' })]) as Array<
      ReturnType<typeof entityWith>
    >
    expect(
      (entity.polygon.heightReference as { getValue: () => unknown }).getValue(),
    ).toBe(HeightReference.CLAMP_TO_GROUND)
  })
})

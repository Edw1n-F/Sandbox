/**
 * The building surface shader, and the reason a *view mode* can be switched
 * without touching the geometry.
 *
 * ## Why a custom fabric at all
 *
 * The look OSIRIS gets out of MapLibre is a dark volume traced with a thin neon
 * rim, plus a bloom pass so the rim glows. Cesium 1.146 has no stock material for
 * it: `ColorMaterialProperty` is a flat lit colour, `PolygonOutlineMaterialProperty`
 * does not exist in this version, and the alternatives (a glTF mesh, a
 * classification primitive) each give up something this tool needs. A custom
 * fabric keeps every envelope an editable `Entity`, which is the entire reason
 * `cityModel.ts` is built on entities rather than on a mesh.
 *
 * ## How a custom material attaches, in 1.146
 *
 * Not `entity.polygon.material = new Material(...)`. That throws: the property
 * setter only coerces values that have no `getValue`, `Material` has none, so it
 * falls through to `createMaterialProperty`, which rejects anything that is not a
 * `Color` or a texture source.
 *
 * The entity layer wants the `MaterialProperty` *protocol*: an object with
 * `getType(time)` naming a registered material type, and `getValue(time, uniforms)`
 * writing into that type's uniform bag. `MaterialProperty` itself is abstract here
 * and its constructor throws, so the protocol is implemented directly.
 *
 * ## Why the uniforms are shared by reference
 *
 * `DynamicGeometryUpdater.update()` reads the material only when it rebuilds the
 * primitive, so no later `getValue` will happen — a mode switch would otherwise
 * mean rebuilding 2,639 primitives. So `getValue` hands Cesium *this module's*
 * uniform objects rather than copies of their values, and Cesium's
 * `UniformFloatVec3.set()` reads `.value` off them at draw time. A mode switch is
 * then a float write per building, visible on the next frame.
 *
 * ## Why emission rather than diffuse
 *
 * `czm_phong` is `ambient + emission + diffuse·lambert·light + specular`, so with
 * `diffuse` and `specular` at zero the output *is* `emission` and does not depend
 * on the sun. That matters because the tactical mode dims the sky: a shaded
 * material would go dark at exactly the moment the scene did.
 */
import { Color, Event, Material } from 'cesium'

/**
 * Which of the four visual states an envelope is in.
 *
 * State is carried by the **rim colour and intensity**, never by the body,
 * because the body carries the height ramp. A state-coloured body would destroy
 * the one thing the tactical mode exists to show.
 */
export type BuildingState = 'base' | 'selected' | 'edited' | 'placed'

/** Rim colour and glow per state. */
export const TECH_STATES: Record<BuildingState, { edge: string; glow: number }> = {
  base: { edge: '#3fe0ff', glow: 0.85 },
  selected: { edge: '#ffffff', glow: 1.5 },
  edited: { edge: '#ff9d2e', glow: 1.05 },
  placed: { edge: '#5cff9d', glow: 1.15 },
}

export interface BuildingStyle {
  /** Flat albedo, used only by the survey branch. */
  body: Color
  /** Neon rim and grid colour — this is what encodes the state. */
  edge: Color
  /** Rim intensity. Bumped for the selected envelope so a pick is unmistakable. */
  glow: number
  /** Height as 0..1 across the model's range, for the tactical ramp. */
  height01: number
}

/** Mode-level switches, shared by every envelope in the model. */
export interface MaterialLook {
  /** 1 = Cesium-lit survey body, 0 = emissive tactical body. */
  lit: boolean
  /** Roof/facade grid intensity. 0 in survey: the grid is the mode's signature. */
  grid: number
}

/** A unique type name. One registration, so every envelope shares one program. */
const TYPE = 'HskTechMassing'

const SOURCE = `
czm_material czm_getMaterial(czm_materialInput materialInput)
{
    czm_material material = czm_getDefaultMaterial(materialInput);

    // scalar, not vec3: in this Cesium version czm_material.specular is the
    // specular *intensity* and shininess is a separate float. Writing vec3(0.0)
    // fails to compile with "cannot convert from 'const 3-component vector of
    // float' to 'highp float'", and it takes the whole render loop down with it.
    material.specular = 0.0;
    material.shininess = 0.0;
    material.alpha = 1.0;

    // ---- survey: hand the colour back to Cesium's lighting -------------------
    // A uniform, not a second material: a second material would mean a second
    // Primitive per envelope and a rebuild storm on every switch.
    if (u_lit > 0.5) {
        material.diffuse = u_body;
        material.emission = vec3(0.0);
        return material;
    }

    // ---- tactical ------------------------------------------------------------
    // Grazing-angle term. Facades seen edge-on light up while roofs facing the
    // camera stay dark, which is what turns a heap of boxes into a wireframe
    // city, and it costs no extra geometry.
    float facing = abs(dot(normalize(materialInput.normalEC), normalize(materialInput.positionToEyeEC)));
    float fresnel = pow(1.0 - facing, 2.5);

    // How much of this footprint one pixel spans, from the screen-space derivative
    // of st. This is what makes the mode work at both scales. An envelope is about
    // three pixels wide from the district framing, so a rim sized in st-space
    // floods its whole footprint and 2,639 buildings collapse into one flat cyan
    // blob with the height ramp invisible underneath -- which is exactly what the
    // first version of this shader did. Fading the rim and the grid out as the
    // footprint approaches pixel size hands the far view to the body colour, which
    // is what carries the ramp, and lets the wireframe emerge as you approach.
    // Same reason a line shader needs one.
    vec2 footprint = fwidth(materialInput.st);
    float detail = 1.0 - smoothstep(0.015, 0.22, max(footprint.x, footprint.y));

    // Which face is this -- roof or wall? Compared against the geodetic surface
    // normal rather than a fixed axis, because on a curved globe "up" in ECEF is
    // nowhere near Z and a constant-axis test paints every wall as a roof at this
    // latitude. The unit-sphere form of the built-in is deliberate and matches how
    // Cesium itself uses it: only the direction matters here, and the
    // sphere-versus-ellipsoid difference is a fraction of a degree.
    //
    // Everything below is gated on this, and it is the single most important line in
    // the shader. Cesium derives st from a *planar* projection of the polygon, so on
    // an extruded wall it is one constant per face. Any st-driven term -- outline or
    // grid -- therefore evaluates to the same value across an entire wall: either the
    // whole wall lights up or none of it does. Un-gated, that painted whole facades
    // in the rim colour and the mass became glowing glass instead of a dark volume
    // traced in neon.
    vec3 positionWC = (czm_inverseView * vec4(materialInput.positionToEyeEC, 1.0)).xyz;
    vec3 normalWC = normalize((czm_inverseView * vec4(materialInput.normalEC, 0.0)).xyz);
    float roofness = smoothstep(0.35, 0.8,
        abs(dot(normalWC, czm_geodeticSurfaceNormal(positionWC, vec3(0.0), vec3(1.0)))));

    // Footprint outline. Cesium remaps st onto this primitive's own oriented
    // bounding box and every envelope is its own primitive, so 0 and 1 are *this*
    // building's edges: an outline can never bleed onto a neighbour.
    //
    // Narrow on purpose. This is a line, not a border: at 2-5% of the footprint it
    // becomes a slab of colour on anything but the smallest envelope, and since
    // nothing tone-maps here, an over-bright edge simply clips to white.
    vec2 toEdge = min(materialInput.st, 1.0 - materialInput.st);
    float rim = (1.0 - smoothstep(0.008, 0.028, min(toEdge.x, toEdge.y))) * detail * roofness;

    // Roof panel joints, roofs only.
    vec2 cell = abs(fract(materialInput.st * 6.0 + 0.5) - 0.5);
    float grid = (1.0 - smoothstep(0.42, 0.5, max(cell.x, cell.y))) * detail * roofness;

    // Quantised height ramp: a skyline readable at a glance. Quantised on purpose
    // -- a continuous ramp over 2,639 small footprints turns into noise, while six
    // bands still survive being read from 4 km up. Gamma-lifted because most of
    // this model sits in its lower third, and a linear ramp leaves the whole
    // district on one indistinguishable step.
    float band = floor(clamp(u_height01, 0.0, 0.999) * 6.0) / 5.0;
    band = pow(band, 0.7);
    vec3 body = mix(vec3(0.05, 0.36, 0.55), vec3(1.0, 0.72, 0.24), band);

    // Walls get no st term at all, so form has to come from the grazing angle
    // instead: a wall square-on to the camera is near black, one seen edge-on lights
    // up. That gradient is what makes a facade read as a surface rather than a void,
    // and it costs nothing because the term is already there for the silhouette.
    float glow = rim * u_glow + grid * u_grid * 0.5;

    material.diffuse = vec3(0.0);
    // Brightness budget: with no tone mapping, anything over 1.0 clips to white, so
    // the rim alone is allowed near the top and the body stays well under it. That
    // split is the whole effect -- dark masses, bright edges.
    //
    // The band term on the end is not decoration. Scaling every envelope by the same
    // factor leaves a district of similar-height blocks as one flat colour, which is
    // the thing the ramp exists to prevent: the tall end has to be allowed to
    // dominate, so a 130 m tower reads as a landmark from the district framing rather
    // than as one more dark box.
    material.emission = body * (0.16 + 0.62 * fresnel) * mix(1.0, 1.25, roofness)
        * (0.8 + 0.6 * band) + u_edge * glow;
    return material;
}
`

interface Uniform {
  type: string
  value: unknown
}

type UniformBag = Record<string, Uniform>

/**
 * The slice of `MaterialProperty` the entity layer actually calls.
 *
 * Declared rather than extended: `MaterialProperty`'s constructor throws
 * `throwInstantiationError` in 1.146, so there is nothing to extend.
 *
 * `definitionChanged` is required and must **never be raised**. `Batch2`, the
 * batching layer that turns entity geometry into primitives, subscribes to it in
 * its constructor and sets `invalidated = true` when it fires — which rebuilds the
 * primitive. That is precisely the rebuild storm this design exists to avoid, so
 * the event exists only to satisfy the constructor.
 */
export interface FabricMaterialProperty {
  readonly isConstant: boolean
  readonly definitionChanged: Event
  getType: () => string
  getValue: (time?: unknown, result?: unknown) => unknown
  equals: (other: unknown) => boolean
}

/**
 * Register the fabric type exactly once, and make it opaque.
 *
 * `Material.fromType(TYPE)` — how the entity layer materialises a custom fabric —
 * throws unless the type is already in Cesium's cache, and constructing a Material
 * is what puts it there.
 *
 * The second half is the important half. `fromType` builds its Material with no
 * `translucent` option, so `initializeMaterial` falls through to "`_translucentFunctions`
 * is empty, therefore translucent" and concludes true. That default is reasonable
 * for a fabric whose alpha nobody has inspected and wrong here: translucent
 * primitives do not write depth, so 2,639 envelopes cannot occlude one another, the
 * street map shows through the district, and every frame sorts 2,639 boxes.
 *
 * Passing `translucent: false` to the constructor does *not* fix it, because the
 * cache entry is written before that flag is resolved and the `fromType` clone never
 * sees it. So the entry is re-registered in the same shape Cesium uses for its own
 * materials, carrying an explicit `translucent`. `_materialCache` is private, hence
 * the cast: it is one write at startup, it is the same structure the built-in
 * registrations populate, and the alternative measured on screen is a see-through
 * city.
 */
let registered = false
function registerType(): void {
  if (registered) return
  const fabric = {
    type: TYPE,
    // Placeholders: they exist so the generated shader declares the uniforms. Every
    // envelope overwrites them through its own property.
    uniforms: {
      u_lit: { type: 'float', value: 1 },
      u_body: { type: 'vec3', value: new Color(0.7, 0.7, 0.7) },
      u_edge: { type: 'vec3', value: new Color(0, 0, 0) },
      u_glow: { type: 'float', value: 0 },
      u_grid: { type: 'float', value: 0 },
      u_height01: { type: 'float', value: 0 },
    },
    source: SOURCE,
  }
  // Builds the shader, declares the uniforms, and registers the type.
  new Material({ fabric })
  const cache = (Material as unknown as {
    _materialCache: { addMaterial: (type: string, template: unknown) => void }
  })._materialCache
  cache.addMaterial(TYPE, { fabric, translucent: () => false })
  registered = true
}

export interface BuildingMaterial {
  /** Ready to assign to `entity.polygon.material`. */
  readonly property: FabricMaterialProperty
  /** Rewrite this envelope's style. Uniform writes only, so no rebuild. */
  setStyle: (style: BuildingStyle) => void
  /** Flip between the lit and emissive readings of the same shader. */
  setLook: (look: MaterialLook) => void
}

export function createBuildingMaterial(style: BuildingStyle): BuildingMaterial {
  registerType()

  // Cloned, never aliased: `setStyle` mutates these in place, and the caller's
  // Color is also a palette entry shared with every other envelope.
  const uniforms: UniformBag = {
    u_lit: { type: 'float', value: 1 },
    u_body: { type: 'vec3', value: Color.clone(style.body) },
    u_edge: { type: 'vec3', value: Color.clone(style.edge) },
    u_glow: { type: 'float', value: style.glow },
    u_grid: { type: 'float', value: 0 },
    u_height01: { type: 'float', value: style.height01 },
  }

  const property: FabricMaterialProperty = {
    isConstant: true,
    // Required by `Batch2`'s constructor; deliberately never raised. See the
    // interface doc — raising it would rebuild the primitive on every mode switch.
    definitionChanged: new Event(),
    getType: () => TYPE,
    // Plain values, not the `{ type, value }` wrappers, and handed over by
    // reference -- see the module header. Two details are load-bearing:
    // `Material._uniforms[name]()` passes `material.uniforms[name]` straight to
    // the GL uniform setter, which accepts a `Color` or a `Cartesian3` and
    // throws "Invalid vec3 value" on a wrapper object; and because the reference
    // is shared, a later `setStyle` is picked up with no primitive rebuild.
    getValue: (_time?: unknown, result?: unknown) => {
      const target = (result ?? {}) as Record<string, unknown>
      for (const name of Object.keys(uniforms)) target[name] = uniforms[name].value
      return target
    },
    equals: (other: unknown) => other === property,
  }

  return {
    property,
    setStyle: (next) => {
      Color.clone(next.body, uniforms.u_body.value as Color)
      Color.clone(next.edge, uniforms.u_edge.value as Color)
      uniforms.u_glow.value = next.glow
      uniforms.u_height01.value = next.height01
    },
    setLook: (look) => {
      uniforms.u_lit.value = look.lit ? 1 : 0
      uniforms.u_grid.value = look.grid
    },
  }
}

/**
 * Flip a whole model between the two modes.
 *
 * Takes the map rather than a list because the caller owns it: `cityModel.ts`
 * already keeps one entry per envelope, and pushing a second collection through
 * the handle API to change two floats would be ceremony.
 */
export function setModelLook(
  materials: Map<string, BuildingMaterial>,
  look: MaterialLook,
): void {
  for (const entry of materials.values()) entry.setLook(look)
}

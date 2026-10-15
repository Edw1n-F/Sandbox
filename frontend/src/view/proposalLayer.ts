/**
 * The unpersisted **draft** block layer (DESIGN §2.4).
 *
 * Before the user confirms, a proposal is drawn as a *depiction*: the same
 * `STYLE_FOR_KIND` colour as the real block, but translucent (`opacity × 0.5`)
 * and low (`extrusionHeight × 0.4`) with a dashed white outline, so it reads as
 * "proposed, not built". After confirmation the real 2.5D block is drawn by
 * `interventionLayer.ts` at the full extrusion, through the existing path.
 *
 * Three things this layer deliberately reuses rather than re-implements:
 *
 * 1. **The style function.** `applyStyle` is imported from `interventionLayer`
 *    (now exported), so a draft and its confirmed twin cannot be styled by two
 *    different pieces of code -- the only difference is the transform below.
 * 2. **The geometry.** What is drawn is the *served* geometry the server already
 *    clipped and contoured, never the model's `requested` ask (§2.4), so what
 *    the user sees before confirming is exactly what will be persisted.
 * 3. **The load-then-add order**, for the same reason `interventionLayer` does
 *    it: with `requestRenderMode` on, an entity that arrives asynchronously
 *    never paints until something requests a frame.
 *
 * Cesium's polygon outline cannot be dashed, so the dashed ring is a companion
 * polyline drawn at the draft top. That is the one piece of drawing this layer
 * owns.
 */
import {
  Cartesian3,
  Cartographic,
  Color,
  ConstantProperty,
  GeoJsonDataSource,
  PolylineDashMaterialProperty,
  PolylineGraphics,
  type Viewer,
} from 'cesium'

import type { InterventionProposal } from '@/api/planApi'
import { applyStyle } from './interventionLayer'

export interface ProposalLayerHandle {
  dispose: () => void
}

const EMPTY_HANDLE: ProposalLayerHandle = { dispose: () => undefined }

/** A draft is drawn at this fraction of the stored opacity (DESIGN §2.4). */
const DRAFT_OPACITY_FRACTION = 0.5
/** ...and this fraction of the stored extrusion height (DESIGN §2.4). */
const DRAFT_EXTRUSION_FRACTION = 0.4

interface StyleSpec {
  color?: string
  opacity?: number
  extrusionHeight?: number
}

/**
 * The draft style for one proposal: the kind's colour, halved opacity and
 * 40 % extrusion. A kind with no extrusion stays flat (translucent patch),
 * which is the honest depiction of a surface that has no height.
 */
function draftStyle(style: StyleSpec | undefined): StyleSpec {
  const base = style ?? {}
  const draft: StyleSpec = {
    color: base.color ?? '#8250df',
    opacity: (base.opacity ?? 0.6) * DRAFT_OPACITY_FRACTION,
  }
  if (base.extrusionHeight) {
    draft.extrusionHeight = base.extrusionHeight * DRAFT_EXTRUSION_FRACTION
  }
  return draft
}

/** One stable id per proposal, so a re-render reuses the entity rather than duplicating it. */
function proposalId(proposal: InterventionProposal, index: number): string {
  return proposal.siteId ?? `${proposal.interventionType}#draft-${index}`
}

/** Wrap the proposals in the FeatureCollection `GeoJsonDataSource` wants. */
function toFeatureCollection(proposals: InterventionProposal[]): {
  type: 'FeatureCollection'
  features: unknown[]
} {
  return {
    type: 'FeatureCollection',
    features: proposals.map((proposal, index) => ({
      type: 'Feature',
      id: proposalId(proposal, index),
      geometry: proposal.geometry,
      properties: {
        id: proposalId(proposal, index),
        interventionType: proposal.interventionType,
        style: draftStyle(proposal.style as StyleSpec | undefined),
      },
    })),
  }
}

/**
 * Add one dashed white ring on top of each draft block.
 *
 * The ring is lifted to the draft's extrusion height so it sits on the top face
 * rather than being buried in the block. Positions come back from the entity's
 * own polygon hierarchy, so the ring follows whatever Cesium built from the
 * served geometry -- there is no second copy of the coordinates.
 */
function addDashedOutline(dataSource: GeoJsonDataSource): void {
  for (const entity of dataSource.entities.values) {
    const polygon = entity.polygon
    if (!polygon) continue
    const style = entity.properties?.style?.getValue() as StyleSpec | undefined
    const height = style?.extrusionHeight ?? 0
    const hierarchy = polygon.hierarchy?.getValue()
    const positions = hierarchy?.positions as Cartesian3[] | undefined
    if (!positions || positions.length === 0) continue

    const ring = positions.map((position) => {
      const carto = Cartographic.fromCartesian(position)
      return Cartesian3.fromRadians(carto.longitude, carto.latitude, height)
    })
    ring.push(ring[0]) // close the ring

    entity.polyline = new PolylineGraphics({
      positions: new ConstantProperty(ring),
      width: new ConstantProperty(1.5),
      material: new PolylineDashMaterialProperty({
        color: Color.WHITE.withAlpha(0.9),
        dashLength: 12,
      }),
      clampToGround: new ConstantProperty(false),
    })
  }
}

/**
 * Draw the proposals as draft 2.5D blocks. Returns a disposer.
 *
 * An empty list is a valid, renderable answer -- nothing to do.
 */
export async function addProposals(
  viewer: Viewer,
  proposals: InterventionProposal[],
): Promise<ProposalLayerHandle> {
  if (!proposals.length) return EMPTY_HANDLE

  const dataSource = new GeoJsonDataSource('proposal-preview')
  try {
    await dataSource.load(toFeatureCollection(proposals) as unknown as object, {
      clampToGround: true,
    })
  } catch (err) {
    console.warn('[sandbox] proposal preview failed to load:', err)
    return EMPTY_HANDLE
  }

  applyStyle(dataSource)
  addDashedOutline(dataSource)

  if (viewer.isDestroyed()) return EMPTY_HANDLE
  await viewer.dataSources.add(dataSource)
  viewer.scene.requestRender()

  return {
    dispose: () => {
      if (!viewer.isDestroyed() && viewer.dataSources.contains(dataSource)) {
        viewer.dataSources.remove(dataSource, true)
      }
    },
  }
}

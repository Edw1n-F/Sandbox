#!/usr/bin/env python
"""Convert 6-NW-12D BUILDING glTF meshes into a Cesium 3D Tiles tileset.

    python scripts/build_3dtiles.py

## What this does

Reads all 390 building glTF files from ``6-NW-12D/BUILDING/``, extracts their
full mesh data (vertices, normals, UVs, indices) plus textures, transforms
every vertex from glTF-local coordinates to WGS84 ECEF (Earth-Centered
Earth-Fixed), merges them into a single glTF binary payload, and wraps it as a
``.b3dm`` file with a ``tileset.json`` manifest.

The output is a real 3D Tiles tileset that Cesium renders with one draw call,
complete with original photogrammetric textures -- not the 2.5D extruded
rectangles that ``ingest_buildings_gltf.py`` produces.

## Coordinate pipeline (per vertex)

    glTF local (lx, ly, lz)
        →  _parse_matrix() gives (easting_o, northing_o, elev_o)
        →  HK1980 absolute: E = easting_o + lx, N = northing_o + ly, h = elev_o + lz
        →  pyproj: EPSG:2326 → EPSG:4326 gives (lon, lat)
        →  _lla_to_ecef(): WGS84 ellipsoid → ECEF (X, Y, Z)

Because vertices are already in ECEF, ``tileset.json`` uses an identity
``transform`` matrix.

## Why no external toolchain

``py3dtiles`` or ``glTF-Transform`` could do parts of this, but the project's
design philosophy (see ``requirements.txt``) is "pure numpy + pillow, no
extra compiled dependencies". The ``.b3dm`` format is simple enough to write
by hand: a 28-byte header, a Feature Table JSON, a Batch Table JSON, and the
glTF payload. ~300 lines vs a new pip dependency.

## Matrix convention (same as ingest_terrain_gltf.py)

Column-major 4x4: [1,0,0,0, 0,0,-1,0, 0,1,0,0, tx, ty, tz, 1]
  glTF local X  -> HK1980 easting  offset
  glTF local Y  -> HK1980 northing offset
  glTF local Z  -> elevation        offset
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Callable

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

BUILDING_DIR = BACKEND_DIR / "6-NW-12D" / "BUILDING"

# WGS84 ellipsoid constants
_WGS84_A = 6378137.0                       # semi-major axis (metres)
_WGS84_E2 = 6.6943799901413e-3             # first eccentricity squared


# --------------------------------------------------------------------------- #
# Coordinate transforms
# --------------------------------------------------------------------------- #


def _parse_matrix(matrix16: list[float]) -> tuple[float, float, float]:
    """Return (easting, northing, elev_origin) from column-major 4x4 glTF matrix."""
    tx = matrix16[12]
    ty = matrix16[13]
    tz = matrix16[14]
    return tx, -tz, ty


def _hk1980_to_wgs84_batch(
    eastings: np.ndarray, northings: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Transform HK1980 Grid (EPSG:2326) to WGS84 (EPSG:4326)."""
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    lons, lats = tf.transform(eastings, northings)
    return np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64)


def _lla_to_ecef(
    lon: np.ndarray, lat: np.ndarray, h: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """WGS84 lon/lat/height → Earth-Centered Earth-Fixed XYZ.

    Standard geodetic-to-ECEF conversion on the WGS84 ellipsoid. Vectorised
    with numpy so all 390 buildings' vertices (~270k) transform in one pass.
    """
    lon_r = np.radians(lon)
    lat_r = np.radians(lat)
    sin_lat = np.sin(lat_r)
    cos_lat = np.cos(lat_r)
    N = _WGS84_A / np.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    x = (N + h) * cos_lat * np.cos(lon_r)
    y = (N + h) * cos_lat * np.sin(lon_r)
    z = (_WGS84_E2 * (-1.0) + 1.0) * N * sin_lat + h * sin_lat
    return x, y, z


def _enu_matrix(lon0_deg: float, lat0_deg: float, h0: float) -> np.ndarray:
    """Return the 4×4 ENU→ECEF transform for an ENU frame centred at (lon0, lat0, h0).

    Column-major layout (as required by Cesium tileset.json 'transform'):
      col0 = East  unit vector in ECEF
      col1 = North unit vector in ECEF
      col2 = Up    unit vector in ECEF
      col3 = ECEF position of the ENU origin

    The matrix converts local ENU (metres east/north/up from the origin) to
    ECEF.  Written into tileset.json root.transform so Cesium positions the
    whole tile without any per-vertex ECEF arithmetic.
    """
    lon_r = np.radians(lon0_deg)
    lat_r = np.radians(lat0_deg)
    sin_lon = np.sin(lon_r);  cos_lon = np.cos(lon_r)
    sin_lat = np.sin(lat_r);  cos_lat = np.cos(lat_r)

    # East unit vector
    e = np.array([-sin_lon, cos_lon, 0.0])
    # North unit vector
    n = np.array([-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat])
    # Up unit vector
    u = np.array([cos_lat * cos_lon,   cos_lat * sin_lon,  sin_lat])

    # ECEF position of the origin
    ox, oy, oz = _lla_to_ecef(
        np.array([lon0_deg]), np.array([lat0_deg]), np.array([h0])
    )

    # 4×4 column-major (Cesium convention): store column by column
    m = np.zeros(16, dtype=np.float64)
    m[0], m[1], m[2]  = e[0], e[1], e[2]   # col 0 = East
    m[4], m[5], m[6]  = n[0], n[1], n[2]   # col 1 = North
    m[8], m[9], m[10] = u[0], u[1], u[2]   # col 2 = Up
    m[12], m[13], m[14] = float(ox[0]), float(oy[0]), float(oz[0])  # col 3 = origin
    m[15] = 1.0
    return m


def _ecef_to_enu(
    x: np.ndarray, y: np.ndarray, z: np.ndarray,
    lon0_deg: float, lat0_deg: float, h0: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert ECEF XYZ to local ENU coordinates relative to (lon0, lat0, h0).

    Returns (east, north, up) arrays in metres.  Vectorised over all vertices
    of all buildings in one pass.
    """
    lon_r = np.radians(lon0_deg)
    lat_r = np.radians(lat0_deg)
    sin_lon = np.sin(lon_r);  cos_lon = np.cos(lon_r)
    sin_lat = np.sin(lat_r);  cos_lat = np.cos(lat_r)

    ox, oy, oz = _lla_to_ecef(
        np.array([lon0_deg]), np.array([lat0_deg]), np.array([h0])
    )
    dx = x - float(ox[0])
    dy = y - float(oy[0])
    dz = z - float(oz[0])

    east  = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up    =  cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return east, north, up


# --------------------------------------------------------------------------- #
# glTF binary buffer reading
# --------------------------------------------------------------------------- #


def _read_accessor(
    gltf: dict, bin_data: bytes, accessor_idx: int
) -> np.ndarray:
    """Read an accessor's data from the .bin file as a numpy array.

    Returns a flat array; the caller reshapes based on the accessor ``type``
    (VEC3 → 3 columns, VEC2 → 2, SCALAR → 1).
    """
    acc = gltf["accessors"][accessor_idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = gltf["buffers"][0]

    count = acc["count"]
    comp_type = acc["componentType"]
    acc_type = acc["type"]
    byte_offset = bv["byteOffset"] + acc.get("byteOffset", 0)

    # Map glTF componentType to numpy dtype
    # 5126 = FLOAT (float32), 5123 = UNSIGNED_SHORT (uint16),
    # 5121 = UNSIGNED_BYTE (uint8), 5125 = UNSIGNED_INT (uint32)
    if comp_type == 5126:
        dtype = np.float32
        bytes_per = 4
    elif comp_type == 5123:
        dtype = np.uint16
        bytes_per = 2
    elif comp_type == 5121:
        dtype = np.uint8
        bytes_per = 1
    elif comp_type == 5125:
        dtype = np.uint32
        bytes_per = 4
    else:
        raise ValueError(f"Unsupported componentType {comp_type}")

    n_components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc_type]
    total = count * n_components
    flat = np.frombuffer(
        bin_data, dtype=dtype, count=total, offset=byte_offset
    ).copy()
    return flat.reshape(count, n_components) if n_components > 1 else flat


def _parse_building_gltf(gltf_path: Path) -> dict | None:
    """Read one building glTF's full mesh + metadata.

    Returns a dict with:
      positions (N,3) float32  — local coordinates
      normals   (N,3) float32  — local normals (kept as-is; ECEF rotation not applied)
      texcoords (N,2) float32  — UV coordinates
      indices   (M,)  uint16   — triangle indices
      material_idx             — index into the glTF materials array
      texture_uri              — relative path to the JPEG texture (or None)
      texture_name             — material name (for batch table)
      easting_o, northing_o, elev_o — HK1980 origin from node matrix
      building_id              — directory stem (e.g. B172473280401062A0)

    Returns None on any error, so one bad file does not abort the batch.
    """
    try:
        with gltf_path.open("r", encoding="utf-8") as fh:
            gltf = json.load(fh)
    except Exception:  # noqa: BLE001
        return None

    nodes = gltf.get("nodes", [])
    if not nodes or "matrix" not in nodes[0]:
        return None
    matrix16 = nodes[0]["matrix"]
    easting_o, northing_o, elev_o = _parse_matrix(matrix16)

    accessors = gltf.get("accessors", [])
    meshes = gltf.get("meshes", [])
    if not accessors or not meshes:
        return None

    # The mesh node is a child of node 0 (children: [1], node 1 has mesh: 0)
    # Primitive attributes: POSITION=0, NORMAL=1, TEXCOORD_0=2, indices=3
    prim = meshes[0]["primitives"][0]
    pos_idx = prim["attributes"]["POSITION"]
    nrm_idx = prim["attributes"].get("NORMAL")
    uv_idx = prim["attributes"].get("TEXCOORD_0")
    idx_idx = prim.get("indices")

    bin_uri = gltf["buffers"][0]["uri"]
    bin_path = gltf_path.parent / bin_uri
    try:
        bin_data = bin_path.read_bytes()
    except Exception:  # noqa: BLE001
        return None

    positions = _read_accessor(gltf, bin_data, pos_idx).astype(np.float32)
    normals = (
        _read_accessor(gltf, bin_data, nrm_idx).astype(np.float32)
        if nrm_idx is not None
        else None
    )
    texcoords = (
        _read_accessor(gltf, bin_data, uv_idx).astype(np.float32)
        if uv_idx is not None
        else None
    )
    indices = (
        _read_accessor(gltf, bin_data, idx_idx).astype(np.uint32)
        if idx_idx is not None
        else np.arange(len(positions), dtype=np.uint32)
    )

    # Material / texture
    mat_idx = prim.get("material")
    texture_uri = None
    material_name = ""
    if mat_idx is not None and gltf.get("materials"):
        mat = gltf["materials"][mat_idx]
        material_name = mat.get("name", "")
        bct = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture", {})
        tex_idx = bct.get("index")
        if tex_idx is not None and gltf.get("textures"):
            src_idx = gltf["textures"][tex_idx].get("source")
            if src_idx is not None and gltf.get("images"):
                texture_uri = gltf["images"][src_idx].get("uri")

    return {
        "building_id": gltf_path.stem,
        "positions": positions,
        "normals": normals,
        "texcoords": texcoords,
        "indices": indices.astype(np.uint32),
        "material_name": material_name,
        "texture_uri": texture_uri,
        "easting_o": easting_o,
        "northing_o": northing_o,
        "elev_o": elev_o,
        "gltf_dir": gltf_path.parent,
    }


# --------------------------------------------------------------------------- #
# Build merged glTF binary
# --------------------------------------------------------------------------- #


def _build_merged_gltf(buildings: list[dict]) -> tuple[bytes, dict, list[str]]:
    """Merge all building meshes into one glTF 2.0 binary (GLB).

    Returns (glb_bytes, gltf_json, texture_uris).

    The strategy:
    - Concatenate all POSITION, NORMAL, TEXCOORD, indices data into one binary
      buffer, with each building offset properly.
    - Each building becomes one mesh with one primitive.
    - Each building gets its own material referencing its own texture image.
    - All vertices are already in ECEF (transformed before this function).
    - Normals are kept as-is from the source glTF. They are local-space normals
      that won't be geometrically correct in ECEF, but for a massing
      visualisation they are adequate — Cesium will still shade the surfaces.
    """
    all_buffers = []  # list of (bytes, target)
    all_buffer_views = []  # list of dict
    all_accessors = []  # list of dict
    all_meshes = []
    all_materials = []
    all_images = []
    all_textures = []
    all_samplers = []
    all_nodes = []

    bin_offset = 0
    image_index_map = {}  # texture_uri -> image index
    material_index_map = {}  # (material_name, texture_uri) -> material index

    for bldg in buildings:
        positions = bldg["positions"]  # already ECEF float32 (N, 3)
        normals = bldg["normals"]      # float32 (N, 3) or None
        texcoords = bldg["texcoords"]  # float32 (N, 2) or None
        indices = bldg["indices"]      # uint32 (M,)
        n_verts = len(positions)
        n_indices = len(indices)

        # --- POSITION bufferView ---
        pos_bytes = positions.astype(np.float32).tobytes()
        pos_bv_idx = len(all_buffer_views)
        all_buffer_views.append({
            "buffer": 0,
            "byteOffset": bin_offset,
            "byteLength": len(pos_bytes),
            "target": 34962,  # ARRAY_BUFFER
        })
        pos_acc_idx = len(all_accessors)
        pos_min = positions.min(axis=0).tolist()
        pos_max = positions.max(axis=0).tolist()
        all_accessors.append({
            "bufferView": pos_bv_idx,
            "byteOffset": 0,
            "componentType": 5126,
            "count": n_verts,
            "type": "VEC3",
            "min": [round(v, 3) for v in pos_min],
            "max": [round(v, 3) for v in pos_max],
        })
        all_buffers.append(pos_bytes)
        bin_offset += len(pos_bytes)

        # Pad to 4-byte boundary
        if bin_offset % 4:
            pad = 4 - (bin_offset % 4)
            all_buffers.append(b"\x00" * pad)
            bin_offset += pad

        # --- NORMAL bufferView (optional) ---
        nrm_acc_idx = None
        if normals is not None:
            nrm_bytes = normals.astype(np.float32).tobytes()
            nrm_bv_idx = len(all_buffer_views)
            all_buffer_views.append({
                "buffer": 0,
                "byteOffset": bin_offset,
                "byteLength": len(nrm_bytes),
                "target": 34962,
            })
            nrm_acc_idx = len(all_accessors)
            all_accessors.append({
                "bufferView": nrm_bv_idx,
                "byteOffset": 0,
                "componentType": 5126,
                "count": n_verts,
                "type": "VEC3",
            })
            all_buffers.append(nrm_bytes)
            bin_offset += len(nrm_bytes)
            if bin_offset % 4:
                pad = 4 - (bin_offset % 4)
                all_buffers.append(b"\x00" * pad)
                bin_offset += pad

        # --- TEXCOORD_0 bufferView (optional) ---
        uv_acc_idx = None
        if texcoords is not None:
            uv_bytes = texcoords.astype(np.float32).tobytes()
            uv_bv_idx = len(all_buffer_views)
            all_buffer_views.append({
                "buffer": 0,
                "byteOffset": bin_offset,
                "byteLength": len(uv_bytes),
                "target": 34962,
            })
            uv_acc_idx = len(all_accessors)
            all_accessors.append({
                "bufferView": uv_bv_idx,
                "byteOffset": 0,
                "componentType": 5126,
                "count": n_verts,
                "type": "VEC2",
            })
            all_buffers.append(uv_bytes)
            bin_offset += len(uv_bytes)
            if bin_offset % 4:
                pad = 4 - (bin_offset % 4)
                all_buffers.append(b"\x00" * pad)
                bin_offset += pad

        # --- INDICES bufferView ---
        # Use uint16 if max index < 65536, else uint32
        max_idx = int(indices.max())
        if max_idx < 65536:
            idx_bytes = indices.astype(np.uint16).tobytes()
            idx_comp = 5123  # UNSIGNED_SHORT
        else:
            idx_bytes = indices.astype(np.uint32).tobytes()
            idx_comp = 5125  # UNSIGNED_INT
        idx_bv_idx = len(all_buffer_views)
        all_buffer_views.append({
            "buffer": 0,
            "byteOffset": bin_offset,
            "byteLength": len(idx_bytes),
            "target": 34963,  # ELEMENT_ARRAY_BUFFER
        })
        idx_acc_idx = len(all_accessors)
        all_accessors.append({
            "bufferView": idx_bv_idx,
            "byteOffset": 0,
            "componentType": idx_comp,
            "count": n_indices,
            "type": "SCALAR",
        })
        all_buffers.append(idx_bytes)
        bin_offset += len(idx_bytes)
        if bin_offset % 4:
            pad = 4 - (bin_offset % 4)
            all_buffers.append(b"\x00" * pad)
            bin_offset += pad

        # --- Material + texture ---
        mat_key = (bldg["material_name"], bldg["texture_uri"])
        if mat_key not in material_index_map:
            mat_idx = len(all_materials)
            material_index_map[mat_key] = mat_idx

            tex_ref = {}
            if bldg["texture_uri"]:
                # Embed the texture image as a bufferView inside the GLB binary,
                # because b3dm glTF content cannot resolve external file URIs.
                if bldg["texture_uri"] not in image_index_map:
                    img_bytes = bldg["gltf_dir"].joinpath(bldg["texture_uri"]).read_bytes()

                    # Pad image data to 4-byte boundary
                    img_bv_idx = len(all_buffer_views)
                    all_buffer_views.append({
                        "buffer": 0,
                        "byteOffset": bin_offset,
                        "byteLength": len(img_bytes),
                    })
                    all_buffers.append(img_bytes)
                    bin_offset += len(img_bytes)
                    if bin_offset % 4:
                        pad = 4 - (bin_offset % 4)
                        all_buffers.append(b"\x00" * pad)
                        bin_offset += pad

                    img_idx = len(all_images)
                    all_images.append({
                        "bufferView": img_bv_idx,
                        "mimeType": "image/jpeg",
                    })
                    image_index_map[bldg["texture_uri"]] = img_idx

                    samp_idx = len(all_samplers)
                    all_samplers.append({
                        "wrapS": 10497,  # REPEAT
                        "wrapT": 10497,
                    })
                    all_textures.append({
                        "source": img_idx,
                        "sampler": samp_idx,
                    })

                tex_ref = {"baseColorTexture": {"index": len(all_textures) - 1}}

            all_materials.append({
                "name": bldg["material_name"] or f"mat-{mat_idx}",
                "pbrMetallicRoughness": {
                    **tex_ref,
                    "roughnessFactor": 0.9,
                    "metallicFactor": 0.1,
                },
            })
        else:
            mat_idx = material_index_map[mat_key]

        # --- Mesh primitive ---
        attrs = {"POSITION": pos_acc_idx}
        if nrm_acc_idx is not None:
            attrs["NORMAL"] = nrm_acc_idx
        if uv_acc_idx is not None:
            attrs["TEXCOORD_0"] = uv_acc_idx

        mesh_idx = len(all_meshes)
        all_meshes.append({
            "primitives": [{
                "attributes": attrs,
                "indices": idx_acc_idx,
                "material": mat_idx,
                "mode": 4,  # TRIANGLES
            }]
        })

        # --- Node (no transform — vertices are already ECEF) ---
        node_idx = len(all_nodes)
        all_nodes.append({
            "name": bldg["building_id"],
            "mesh": mesh_idx,
        })

    # Assemble the glTF JSON
    total_bin = sum(len(b) for b in all_buffers)
    gltf_json = {
        "asset": {"generator": "build_3dtiles.py", "version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(all_nodes)))}],
        "nodes": all_nodes,
        "meshes": all_meshes,
        "materials": all_materials,
        "accessors": all_accessors,
        "bufferViews": all_buffer_views,
        "buffers": [{"byteLength": total_bin}],
    }
    if all_images:
        gltf_json["images"] = all_images
    if all_textures:
        gltf_json["textures"] = all_textures
    if all_samplers:
        gltf_json["samplers"] = all_samplers

    binary = b"".join(all_buffers)
    texture_uris = list(image_index_map.keys())
    return binary, gltf_json, texture_uris


def _gltf_to_glb(gltf_json: dict, binary: bytes) -> bytes:
    """Wrap a glTF JSON + binary into a GLB (binary glTF) container.

    GLB layout:
      [12-byte header: magic, version, totalLength]
      [JSON chunk header: length, type 0x4E4F534A]
      [JSON chunk data, padded with spaces to 4-byte boundary]
      [BIN chunk header: length, type 0x004E4942]
      [BIN chunk data, padded with zeros to 4-byte boundary]
    """
    json_bytes = json.dumps(gltf_json, separators=(",", ":")).encode("utf-8")
    # Pad JSON to 4-byte boundary with spaces (0x20)
    json_pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b"\x20" * json_pad

    # Pad binary to 4-byte boundary with zeros
    bin_pad = (4 - len(binary) % 4) % 4
    binary_padded = binary + b"\x00" * bin_pad

    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary_padded)
    header = struct.pack("<4sII", b"glTF", 2, total_length)
    json_chunk_header = struct.pack("<II", len(json_bytes), 0x4E4F534A)
    bin_chunk_header = struct.pack("<II", len(binary_padded), 0x004E4942)

    return header + json_chunk_header + json_bytes + bin_chunk_header + binary_padded


# --------------------------------------------------------------------------- #
# .b3dm packaging
# --------------------------------------------------------------------------- #


def _pad_to_boundary(data: bytes, boundary: int = 8, pad_char: bytes = b"\x20") -> bytes:
    """Pad data to a boundary with the given character."""
    remainder = len(data) % boundary
    if remainder == 0:
        return data
    return data + pad_char * (boundary - remainder)


def _write_b3dm(
    glb_bytes: bytes,
    batch_count: int,
    batch_names: list[str],
    rtc_center: tuple[float, float, float],
) -> bytes:
    """Wrap a GLB into a .b3dm file with RTC_CENTER in the Feature Table.

    RTC_CENTER shifts the local-space origin to the ECEF centroid of the tile.
    Vertices are stored as offsets from that centre (float32 offsets ~hundreds
    of metres), which preserves sub-centimetre precision without float64.
    Cesium reads RTC_CENTER and translates the tile accordingly.
    """
    # Feature Table: JSON header points to binary payload for RTC_CENTER.
    # The binary payload is three float64 values (24 bytes).
    rt_json = json.dumps({
        "BATCH_LENGTH": batch_count,
        "RTC_CENTER": {"byteOffset": 0},
    }).encode("utf-8")
    rt_json = _pad_to_boundary(rt_json, 8, b"\x20")
    rt_bin = struct.pack("<3d", rtc_center[0], rtc_center[1], rtc_center[2])
    rt_bin = _pad_to_boundary(rt_bin, 8, b"\x00")

    batch_table = json.dumps({"id": batch_names}).encode("utf-8")
    batch_table = _pad_to_boundary(batch_table, 8, b"\x20")

    total_length = 28 + len(rt_json) + len(rt_bin) + len(batch_table) + len(glb_bytes)
    header = struct.pack(
        "<4s6I",
        b"b3dm",
        1,  # version
        total_length,
        len(rt_json),
        len(rt_bin),
        len(batch_table),
        0,  # batch table binary
    )
    return header + rt_json + rt_bin + batch_table + glb_bytes


# --------------------------------------------------------------------------- #
# Bounding volume
# --------------------------------------------------------------------------- #


def _compute_bounding_box_enu(
    all_e: np.ndarray, all_n: np.ndarray, all_u: np.ndarray
) -> list[float]:
    """Compute a 3D Tiles ``box`` bounding volume in ENU local coordinates.

    The box format: [cx, cy, cz,  hx, 0, 0,  0, hy, 0,  0, 0, hz]
    where (cx,cy,cz) is the ENU centre and (hx,hy,hz) are half-extents.
    Used together with root.transform (ENU→ECEF), so Cesium positions the
    box correctly in world space.
    """
    cx = float((all_e.min() + all_e.max()) / 2.0)
    cy = float((all_n.min() + all_n.max()) / 2.0)
    cz = float((all_u.min() + all_u.max()) / 2.0)
    hx = float((all_e.max() - all_e.min()) / 2.0)
    hy = float((all_n.max() - all_n.min()) / 2.0)
    hz = float((all_u.max() - all_u.min()) / 2.0)
    return [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _load_dem_sampler(
    dem_path: Path, scene_json_path: Path
) -> "Callable[[float, float], float] | None":
    """Return a bilinear sampler dem_at(lon, lat) -> metres, or None on error."""
    try:
        dem = np.load(str(dem_path))
        with open(scene_json_path, encoding="utf-8") as fh:
            scene = json.load(fh)
        corners = scene["bbox"]["coordinates"][0]
        lons_c = [c[0] for c in corners]
        lats_c = [c[1] for c in corners]
        lon_min, lon_max = min(lons_c), max(lons_c)
        lat_min, lat_max = min(lats_c), max(lats_c)
        rows, cols = dem.shape

        def _sample(lon: float, lat: float) -> float:
            fc = (lon - lon_min) / (lon_max - lon_min) * cols - 0.5
            fr = (lat_max - lat) / (lat_max - lat_min) * rows - 0.5
            c0, r0 = int(fc), int(fr)
            c1, r1 = c0 + 1, r0 + 1
            c0 = max(0, min(c0, cols - 1))
            c1 = max(0, min(c1, cols - 1))
            r0 = max(0, min(r0, rows - 1))
            r1 = max(0, min(r1, rows - 1))
            wc = fc - int(fc)
            wr = fr - int(fr)
            return float(
                dem[r0, c0] * (1 - wr) * (1 - wc)
                + dem[r0, c1] * (1 - wr) * wc
                + dem[r1, c0] * wr * (1 - wc)
                + dem[r1, c1] * wr * wc
            )

        return _sample
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    if not BUILDING_DIR.exists():
        print(f"[3dtiles] ERROR: {BUILDING_DIR} not found")
        return 1

    subdirs = sorted(p for p in BUILDING_DIR.iterdir() if p.is_dir())
    print(f"[3dtiles] found {len(subdirs)} building directories")

    # Load DEM sampler to snap buildings to the terrain surface that Cesium
    # actually renders.  The DEM covers only part of the study area and fills
    # the rest with 0 m or a feather blend; without snapping, buildings in
    # elevated areas fly 5–11 m above the visible ground.
    dem_sampler = _load_dem_sampler(
        settings.data_dir / "dem_hsk.npy",
        settings.data_dir / "scene_hsk.json",
    )
    if dem_sampler is None:
        print("[3dtiles] WARNING: could not load DEM — buildings may float above terrain")

    # --- Parse all buildings ---
    buildings = []
    skipped = 0

    for subdir in subdirs:
        gltf_files = list(subdir.glob("*.gltf"))
        if not gltf_files:
            skipped += 1
            continue

        bldg = _parse_building_gltf(gltf_files[0])
        if bldg is None:
            skipped += 1
            continue

        # Transform vertices: glTF local → HK1980 → WGS84 → ECEF (float64).
        # ENU conversion happens after all buildings are parsed so the origin
        # (bbox centre) is decided once rather than per-building.
        pos = bldg["positions"]  # (N, 3) float32: lx, ly, lz
        lx = pos[:, 0].astype(np.float64)
        ly = pos[:, 1].astype(np.float64)
        lz = pos[:, 2].astype(np.float64)

        easting = bldg["easting_o"] + lx
        northing = bldg["northing_o"] + ly
        elevation = bldg["elev_o"] + lz

        lons, lats = _hk1980_to_wgs84_batch(easting, northing)

        # Snap ground floor to DEM surface.  The glTF lz_min is the lowest
        # vertex (ground floor, always 0 in these files).  If the DEM height
        # at this building's footprint centre differs from elev_o, shift all
        # vertices vertically so the ground floor sits on the rendered terrain.
        if dem_sampler is not None:
            centre_lon = float(lons[np.argmin(np.abs(lz))])
            centre_lat = float(lats[np.argmin(np.abs(lz))])
            dem_ground = dem_sampler(centre_lon, centre_lat)
            elev_shift = dem_ground - bldg["elev_o"]
            elevation = elevation + elev_shift

        ecef_x, ecef_y, ecef_z = _lla_to_ecef(lons, lats, elevation)

        bldg["positions_f64"] = np.column_stack([ecef_x, ecef_y, ecef_z])
        buildings.append(bldg)

    if not buildings:
        print("[3dtiles] ERROR: no buildings parsed")
        return 1

    print(f"[3dtiles] parsed {len(buildings)} buildings, skipped {skipped}")
    total_verts = sum(len(b["positions_f64"]) for b in buildings)
    print(f"[3dtiles] total vertices: {total_verts}")

    # ENU origin: bbox centre at mean DEM ground elevation.
    # Vertices are stored as ENU offsets (East/North/Up in metres) from this
    # origin instead of ECEF offsets from the vertex centroid.  Building height
    # ("Up") is now a pure local-Z value of 4–10 m — float32 at this scale
    # has 1 ULP < 0.001 m, so no precision loss.  The ENU→ECEF transform is
    # written into tileset.json root.transform so Cesium positions the tile
    # without any per-vertex ECEF arithmetic.
    lon0, lat0 = 113.9935, 22.435  # scene centre from scene_hsk.json
    # h0: mean ground elevation from DEM (avoids large Up offsets for ground-
    # level verts).  Fall back to 0 if the DEM is unavailable.
    h0 = 0.0
    try:
        import numpy as _np2
        _dem = _np2.load(str(settings.data_dir / "dem_hsk.npy"))
        _covered = _dem > 0
        h0 = float(_dem[_covered].mean()) if _covered.any() else 0.0
        del _np2, _dem, _covered
    except Exception:  # noqa: BLE001
        pass
    print(f"[3dtiles] ENU origin: lon={lon0} lat={lat0} h0={h0:.2f} m")

    all_e_chunks, all_n_chunks, all_u_chunks = [], [], []
    for bldg in buildings:
        p = bldg.pop("positions_f64")
        ex, nx, ux = _ecef_to_enu(p[:, 0], p[:, 1], p[:, 2], lon0, lat0, h0)
        enu = np.column_stack([ex, nx, ux]).astype(np.float32)
        bldg["positions"] = enu
        all_e_chunks.append(ex)
        all_n_chunks.append(nx)
        all_u_chunks.append(ux)

    all_e = np.concatenate(all_e_chunks)
    all_n = np.concatenate(all_n_chunks)
    all_u = np.concatenate(all_u_chunks)
    print(
        f"[3dtiles] ENU bounds: "
        f"E=[{all_e.min():.1f}, {all_e.max():.1f}]  "
        f"N=[{all_n.min():.1f}, {all_n.max():.1f}]  "
        f"U=[{all_u.min():.2f}, {all_u.max():.2f}]"
    )

    # --- Build merged glTF ---
    print("[3dtiles] merging meshes into glTF binary...")
    binary, gltf_json, texture_uris = _build_merged_gltf(buildings)
    print(f"[3dtiles] glTF: {len(buildings)} meshes, {len(texture_uris)} textures, "
          f"binary={len(binary)} bytes")

    # --- Convert to GLB ---
    glb_bytes = _gltf_to_glb(gltf_json, binary)
    print(f"[3dtiles] GLB: {len(glb_bytes)} bytes")

    # --- Write .b3dm (RTC_CENTER = 0,0,0 — positioning is via transform) ---
    batch_names = [b["building_id"] for b in buildings]
    b3dm_bytes = _write_b3dm(glb_bytes, len(buildings), batch_names, (0.0, 0.0, 0.0))
    print(f"[3dtiles] b3dm: {len(b3dm_bytes)} bytes ({len(buildings)} batches)")

    # --- Output directory ---
    out_dir = settings.rasters_dir / "3dtiles"
    out_dir.mkdir(parents=True, exist_ok=True)

    b3dm_path = out_dir / "buildings.b3dm"
    b3dm_path.write_bytes(b3dm_bytes)
    print(f"[3dtiles] wrote {b3dm_path}")

    # --- tileset.json ---
    bbox = _compute_bounding_box_enu(all_e, all_n, all_u)
    enu_transform = _enu_matrix(lon0, lat0, h0).tolist()
    tileset = {
        # gltfUpAxis=Z tells Cesium not to apply the default Y→Z-up correction.
        # Vertices are stored in ENU (East, North, Up) = Z-up already; the
        # root.transform (ENU→ECEF) handles the final positioning.
        "asset": {"version": "1.0", "gltfUpAxis": "Z"},
        "geometricError": 500.0,
        "root": {
            # ENU→ECEF transform: positions the whole tile from local ENU
            # coordinates into the correct ECEF location on the globe.
            "transform": enu_transform,
            "boundingVolume": {"box": bbox},
            "geometricError": 0.0,
            "refine": "ADD",
            "content": {"uri": "buildings.b3dm"},
        },
    }
    tileset_path = out_dir / "tileset.json"
    tileset_path.write_text(json.dumps(tileset, indent=2), encoding="utf-8")
    print(f"[3dtiles] wrote {tileset_path}")

    # --- Register on scene ---
    try:
        from app import db
        from app.services.scene_service import attach_overlay

        registered = 0
        for doc_path in db.list_docs(settings.scenes_dir):
            doc = db.read_doc(doc_path)
            if not doc or not doc.get("study_area"):
                continue
            attach_overlay(doc["id"], "city_model_3d_path", "rasters/3dtiles/tileset.json")
            registered += 1
        if registered:
            print(f"[3dtiles] registered city_model_3d_path on {registered} scene(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"[3dtiles] could not register on scene: {exc}")

    print("[3dtiles] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

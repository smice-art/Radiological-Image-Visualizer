# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""2D-Bilder (Röntgen usw.) als Bildebene in Originalgröße.

Das Bild wird verlustfrei als 16-Bit-Graustufen-PNG in die .blend gepackt (bleibt beim
Speichern erhalten, ca. 2-8 MB pro Röntgenbild). Window/Level läuft live im Shader
(Map Range auf den normierten Werten), ohne das Bild neu zu schreiben.
"""
import os
import struct
import tempfile
import zlib

import bpy
import numpy as np

PROP = "riv_2d"


def _write_png16(path, a16):
    """Graustufen-PNG mit 16 Bit (reines Python, keine Zusatzbibliothek)."""
    h, w = a16.shape
    be = a16.astype(">u2")
    raw = b"".join(b"\x00" + be[r].tobytes() for r in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 0, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def _material(name, img):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    N, L = mat.node_tree.nodes, mat.node_tree.links
    N.clear()
    tex = N.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.extension = "CLIP"
    tex.location = (-600, 0)
    rng = N.new("ShaderNodeMapRange")
    rng.name = "RIV_Window"
    rng.clamp = True
    rng.location = (-350, 0)
    emi = N.new("ShaderNodeEmission")
    emi.location = (-100, 0)
    out = N.new("ShaderNodeOutputMaterial")
    out.location = (150, 0)
    L.new(tex.outputs["Color"], rng.inputs["Value"])
    L.new(rng.outputs["Result"], emi.inputs["Color"])
    L.new(emi.outputs["Emission"], out.inputs["Surface"])
    return mat


def update_window(obj):
    """Window/Level/Invert eines 2D-Objekts in den Shader übernehmen."""
    if obj is None or not obj.get(PROP) or not obj.data or not obj.data.materials:
        return
    mat = obj.data.materials[0]
    if mat is None or not mat.node_tree:
        return
    rng = mat.node_tree.nodes.get("RIV_Window")
    if rng is None:
        return
    s = obj.riv_2d
    vmin = float(obj.get("riv_vmin", 0.0))
    vmax = float(obj.get("riv_vmax", 1.0))
    span = max(vmax - vmin, 1e-9)
    w = max(s.window, 1e-6)
    rng.inputs["From Min"].default_value = (s.level - w / 2 - vmin) / span
    rng.inputs["From Max"].default_value = (s.level + w / 2 - vmin) / span
    rng.inputs["To Min"].default_value = 1.0 if s.invert else 0.0
    rng.inputs["To Max"].default_value = 0.0 if s.invert else 1.0


def create_plane(arr, window, info, coll, unit_scale, x_start):
    """Bildebene anlegen. arr: [rows, cols] float32 (Zeile 0 = oben). Gibt das Objekt zurück."""
    rows, cols = arr.shape
    vmin = float(np.nanmin(arr))
    vmax = float(np.nanmax(arr))
    if vmax <= vmin:
        vmax = vmin + 1.0
    a16 = np.round((arr - vmin) / (vmax - vmin) * 65535.0).clip(0, 65535).astype(np.uint16)

    n = 1
    while bpy.data.objects.get(f"RIV_2D_{n}"):
        n += 1
    name = f"RIV_2D_{n}"
    path = os.path.join(tempfile.gettempdir(), f"{name}_{os.getpid()}.png")
    _write_png16(path, a16)              # PNG-Zeile 0 = oben -> in Blender ebenfalls oben
    try:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name + "_img"
        img.colorspace_settings.name = "Non-Color"
        img.pack()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    ps_r, ps_c = info.pixel_spacing
    if not ps_r or not ps_c:
        ps_r = ps_c = 0.1               # unbekannt: typische Detektor-Pixelgröße annehmen
    w = cols * ps_c * unit_scale
    h = rows * ps_r * unit_scale
    mesh = bpy.data.meshes.new(name)
    # senkrecht stehend in der XZ-Ebene, Vorderseite nach -Y (zur Frontansicht)
    mesh.from_pydata([(-w / 2, 0, -h / 2), (w / 2, 0, -h / 2), (w / 2, 0, h / 2), (-w / 2, 0, h / 2)],
                     [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    uv.data.foreach_set("uv", [0, 0, 1, 0, 1, 1, 0, 1])
    mesh.materials.append(_material(name + "_mat", img))
    obj = bpy.data.objects.new(name, mesh)
    obj[PROP] = True
    obj["riv_vmin"] = vmin
    obj["riv_vmax"] = vmax
    obj["riv_window_default"] = (float(window[0]), float(window[1]))
    obj["riv_description"] = info.description[:60]
    obj.location = (x_start + w / 2, 0.0, 0.0)
    coll.objects.link(obj)
    obj.riv_2d.level = float(window[0])
    obj.riv_2d.window = float(window[1])
    update_window(obj)
    return obj, (ps_r, ps_c) == (0.1, 0.1) and not info.pixel_spacing[0]


def planes():
    return [o for o in bpy.data.objects if o.get(PROP)]


def next_x(gap, volume_max_x=None):
    """x-Position für die nächste Ebene: rechts neben vorhandenen 2D-Ebenen bzw. dem Volumen."""
    xs = [o.location.x + o.dimensions.x / 2 for o in planes()]
    if volume_max_x is not None:
        xs.append(volume_max_x)
    return (max(xs) + gap) if xs else 0.0

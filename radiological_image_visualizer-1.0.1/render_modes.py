# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Darstellungsmodi: Surface (normales Material), Glass (Röntgen-Look) und Volume (OpenVDB).

Glass: additives Emission + Transparent-Material. Additives Mischen ist reihenfolgeunabhängig,
deshalb gibt es auch bei Millionen Polygonen keine Sortierfehler (anders als bei Alpha-Blending).

Volume: das Volumen wird als OpenVDB-Grid "density" (Rohwerte, HU bei CT) geschrieben und als
Blender-Volume-Objekt geladen. Die Transferfunktion (Wertebereich -> Farbe/Dichte) steckt im
Material und wird über Node-Werte live geändert - kein Neuaufbau beim Verstellen.

Transferfunktion: Wert -> Map Range (Low..High -> 0..1) -> zwei Color Ramps:
"Colors" (Farbe) und "Opacity" (Deckkraft, schwarz = unsichtbar). Beide sind im N-Panel
direkt editierbar; mit mehreren Spitzen in der Opacity-Ramp lassen sich z. B. Gefäße zeigen
und Muskel UND Knochen ausblenden.

Shading:
  GLOW - Emission + Volume Absorption, Emission proportional zur Dichte (klassisches
         Emissions-Absorptions-Modell der medizinischen Volumendarstellung). Unabhängig
         vom Licht, schnell, rauschfrei.
  LIT  - Principled Volume (Streuung): reagiert auf Lampen/HDRI, Schatten im Gewebe,
         plus etwas Eigenleuchten. Fotorealistischer, braucht Licht, langsamer.
"""
import os
import time

import bpy
import numpy as np

from . import isosurface

VOLUME_OBJ = "RIV_Volume"
VOLUME_MAT = "RIV_Volume_mat"
VOLUME_MAT_VERSION = 3
GLASS_MAT = "RIV_Glass_mat"
LIGHT_OBJ = "RIV_Light"
CACHE_DIR = "riv_cache"

# Transferfunktionen: low/high in Datenwerten (HU), colors/opacity als Ramp-Stützpunkte 0..1
VR_PRESETS = {
    "HEART": dict(low=100.0, high=700.0,
                  colors=[(0.0, (0.25, 0.0, 0.0)), (0.3, (0.8, 0.08, 0.04)),
                          (0.65, (1.0, 0.55, 0.25)), (1.0, (1.0, 0.95, 0.85))],
                  opacity=[(0.0, 0.0), (0.12, 0.0), (0.3, 0.5), (0.6, 0.8), (1.0, 1.0)]),
    "VESSELS": dict(low=150.0, high=600.0,
                    colors=[(0.0, (0.35, 0.0, 0.0)), (0.3, (0.85, 0.1, 0.05)), (0.7, (1.0, 0.6, 0.3)),
                            (1.0, (1.0, 0.9, 0.7))],
                    opacity=[(0.0, 0.0), (0.12, 0.0), (0.25, 0.9), (0.65, 0.9), (0.85, 0.0), (1.0, 0.0)]),
    "BONE": dict(low=150.0, high=1200.0,
                 colors=[(0.0, (0.35, 0.18, 0.08)), (0.35, (0.85, 0.7, 0.5)), (1.0, (1.0, 0.98, 0.92))],
                 opacity=[(0.0, 0.0), (0.12, 0.0), (0.4, 0.7), (1.0, 1.0)]),
    "SOFT": dict(low=-250.0, high=300.0,
                 colors=[(0.0, (0.3, 0.12, 0.08)), (0.5, (0.9, 0.55, 0.45)), (1.0, (1.0, 0.9, 0.85))],
                 opacity=[(0.0, 0.0), (0.3, 0.12), (0.6, 0.25), (1.0, 0.8)]),
    "XRAY": dict(low=-300.0, high=1500.0,
                 colors=[(0.0, (0.0, 0.0, 0.0)), (1.0, (1.0, 1.0, 1.0))],
                 opacity=[(0.0, 0.0), (1.0, 1.0)]),
}


# ----------------------------------------------------------------------------------------
# Hilfen
# ----------------------------------------------------------------------------------------

def _new_node_material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True          # ab 5.0 ohne Wirkung, davor nötig
    except Exception:
        pass
    mat.node_tree.nodes.clear()
    return mat


def riv_meshes():
    return [o for o in bpy.data.objects if o.get("riv_mesh") and o.type == "MESH"]


def _set_visible(obj, visible):
    try:
        obj.hide_set(not visible)
    except Exception:
        pass                          # Objekt nicht im aktuellen View Layer
    obj.hide_render = not visible


# ----------------------------------------------------------------------------------------
# Glass
# ----------------------------------------------------------------------------------------

def glass_material():
    mat = bpy.data.materials.get(GLASS_MAT)
    if mat is not None and mat.node_tree and mat.node_tree.nodes.get("RIV_Color"):
        return mat
    mat = _new_node_material(GLASS_MAT)
    N, L = mat.node_tree.nodes, mat.node_tree.links
    lw = N.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    lw.location = (-800, 0)
    edge = N.new("ShaderNodeMath")
    edge.name = "RIV_Edge"
    edge.operation = "POWER"
    edge.inputs[1].default_value = 2.0
    edge.location = (-600, 0)
    rng = N.new("ShaderNodeMapRange")
    rng.name = "RIV_Core"
    rng.inputs["To Min"].default_value = 0.05
    rng.inputs["To Max"].default_value = 1.0
    rng.location = (-400, 0)
    bright = N.new("ShaderNodeMath")
    bright.name = "RIV_Bright"
    bright.operation = "MULTIPLY"
    bright.inputs[1].default_value = 1.0
    bright.location = (-200, 0)
    emi = N.new("ShaderNodeEmission")
    emi.name = "RIV_Color"
    emi.location = (0, 100)
    tr = N.new("ShaderNodeBsdfTransparent")
    tr.location = (0, -100)
    add = N.new("ShaderNodeAddShader")
    add.location = (200, 0)
    out = N.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    L.new(lw.outputs["Facing"], edge.inputs[0])
    L.new(edge.outputs[0], rng.inputs["Value"])
    L.new(rng.outputs[0], bright.inputs[0])
    L.new(bright.outputs[0], emi.inputs["Strength"])
    L.new(emi.outputs[0], add.inputs[0])
    L.new(tr.outputs[0], add.inputs[1])
    L.new(add.outputs[0], out.inputs["Surface"])
    try:
        mat.surface_render_method = "BLENDED"
    except Exception:
        pass
    try:
        mat.use_transparency_overlap = True
    except Exception:
        pass
    mat.use_backface_culling = False
    mat.diffuse_color = (1.0, 0.5, 0.35, 0.3)
    return mat


def update_glass(st):
    mat = bpy.data.materials.get(GLASS_MAT)
    if mat is None or not mat.node_tree:
        return
    N = mat.node_tree.nodes
    if N.get("RIV_Color"):
        N["RIV_Color"].inputs["Color"].default_value = (*st.glass_color, 1.0)
    if N.get("RIV_Bright"):
        N["RIV_Bright"].inputs[1].default_value = st.glass_brightness
    if N.get("RIV_Edge"):
        N["RIV_Edge"].inputs[1].default_value = st.glass_edge
    if N.get("RIV_Core"):
        N["RIV_Core"].inputs["To Min"].default_value = st.glass_core


# ----------------------------------------------------------------------------------------
# Volume
# ----------------------------------------------------------------------------------------

def volume_material():
    mat = bpy.data.materials.get(VOLUME_MAT)
    if mat is not None and mat.node_tree and mat.get("riv_version") == VOLUME_MAT_VERSION:
        return mat
    mat = _new_node_material(VOLUME_MAT)
    mat["riv_version"] = VOLUME_MAT_VERSION
    N, L = mat.node_tree.nodes, mat.node_tree.links

    def node(kind, name, loc, **props):
        n = N.new(kind)
        n.name = name
        n.label = name.replace("RIV_", "")
        n.location = loc
        for k, v in props.items():
            setattr(n, k, v)
        return n

    attr = node("ShaderNodeAttribute", "RIV_Attr", (-1100, 0), attribute_name="density")
    rng = node("ShaderNodeMapRange", "RIV_Range", (-900, 0), clamp=True)
    ramp = node("ShaderNodeValToRGB", "RIV_Ramp", (-650, 250))
    opac = node("ShaderNodeValToRGB", "RIV_Opacity", (-650, -100))
    bw = node("ShaderNodeRGBToBW", "RIV_OpacityBW", (-380, -100))
    dens = node("ShaderNodeMath", "RIV_Density", (-200, -100), operation="MULTIPLY")
    emit = node("ShaderNodeMath", "RIV_Emit", (-200, -300), operation="MULTIPLY")
    # GLOW
    g_emi = node("ShaderNodeEmission", "RIV_GlowEmission", (100, 300))
    g_abs = node("ShaderNodeVolumeAbsorption", "RIV_GlowAbsorb", (100, 150))
    g_abs.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    g_add = node("ShaderNodeAddShader", "RIV_GlowOut", (300, 250))
    # LIT
    l_emit = node("ShaderNodeMath", "RIV_LitEmit", (-200, -500), operation="MULTIPLY")
    l_pv = node("ShaderNodeVolumePrincipled", "RIV_LitPV", (100, -150))
    l_emi = node("ShaderNodeEmission", "RIV_LitEmission", (100, -450))
    l_add = node("ShaderNodeAddShader", "RIV_LitOut", (400, -250))
    out = node("ShaderNodeOutputMaterial", "RIV_Output", (650, 0))

    L.new(attr.outputs["Fac"], rng.inputs["Value"])
    L.new(rng.outputs["Result"], ramp.inputs["Fac"])
    L.new(rng.outputs["Result"], opac.inputs["Fac"])
    L.new(opac.outputs["Color"], bw.inputs["Color"])
    L.new(bw.outputs["Val"], dens.inputs[0])
    L.new(dens.outputs[0], emit.inputs[0])
    L.new(dens.outputs[0], l_emit.inputs[0])
    L.new(ramp.outputs["Color"], g_emi.inputs["Color"])
    L.new(emit.outputs[0], g_emi.inputs["Strength"])
    L.new(dens.outputs[0], g_abs.inputs["Density"])
    L.new(g_emi.outputs[0], g_add.inputs[0])
    L.new(g_abs.outputs[0], g_add.inputs[1])
    L.new(ramp.outputs["Color"], l_pv.inputs["Color"])
    L.new(dens.outputs[0], l_pv.inputs["Density"])
    l_pv.inputs["Emission Strength"].default_value = 0.0
    L.new(ramp.outputs["Color"], l_emi.inputs["Color"])
    L.new(l_emit.outputs[0], l_emi.inputs["Strength"])
    L.new(l_pv.outputs["Volume"], l_add.inputs[0])
    L.new(l_emi.outputs[0], l_add.inputs[1])
    L.new(g_add.outputs[0], out.inputs["Volume"])
    return mat


def _volume_nodes():
    mat = bpy.data.materials.get(VOLUME_MAT)
    if mat is None or not mat.node_tree or mat.get("riv_version") != VOLUME_MAT_VERSION:
        return None
    return mat.node_tree.nodes


def _fill_ramp(ramp_node, stops):
    els = ramp_node.color_ramp.elements
    while len(els) > 1:
        els.remove(els[-1])
    els[0].position = stops[0][0]
    els[0].color = stops[0][1]
    for pos, col in stops[1:]:
        e = els.new(pos)
        e.color = col


def set_ramp(preset):
    """Farb- und Deckkraft-Ramp aus einem Preset füllen."""
    N = _volume_nodes()
    if N is None or preset not in VR_PRESETS:
        return
    p = VR_PRESETS[preset]
    _fill_ramp(N["RIV_Ramp"], [(pos, (*c, 1.0)) for pos, c in p["colors"]])
    _fill_ramp(N["RIV_Opacity"], [(pos, (v, v, v, 1.0)) for pos, v in p["opacity"]])


def ramp_nodes():
    """(Farb-Ramp-Node, Opacity-Ramp-Node) für die Anzeige im Panel, oder (None, None)."""
    N = _volume_nodes()
    if N is None:
        return None, None
    return N.get("RIV_Ramp"), N.get("RIV_Opacity")


def set_shading(mode):
    """GLOW oder LIT: nur die Verbindung zum Material-Ausgang umstecken (beide Zweige bleiben)."""
    mat = bpy.data.materials.get(VOLUME_MAT)
    N = _volume_nodes()
    if N is None:
        return
    L = mat.node_tree.links
    src = N["RIV_LitOut"] if mode == "LIT" else N["RIV_GlowOut"]
    out = N["RIV_Output"]
    for link in list(out.inputs["Volume"].links):
        L.remove(link)
    L.new(src.outputs[0], out.inputs["Volume"])


def update_volume(st):
    """Transferfunktion und Shading-Werte live aus den Einstellungen übernehmen."""
    N = _volume_nodes()
    if N is None:
        return
    lo, hi = st.vr_low, st.vr_high
    if hi <= lo:
        hi = lo + 1.0
    N["RIV_Range"].inputs["From Min"].default_value = lo
    N["RIV_Range"].inputs["From Max"].default_value = hi
    # 0.4 / mm bei Opacity 1, in Blender-Einheiten umgerechnet
    N["RIV_Density"].inputs[1].default_value = st.vr_opacity * 0.4 / max(st.unit_scale_loaded, 1e-9)
    N["RIV_Emit"].inputs[1].default_value = st.vr_brightness
    N["RIV_LitEmit"].inputs[1].default_value = st.vr_brightness * st.vr_glow
    N["RIV_LitPV"].inputs["Anisotropy"].default_value = st.vr_anisotropy


def ensure_light(scene, coll, size):
    """LIT braucht Licht: legt eine Sonne an, falls die Szene keine Lampe hat.
    Gibt das neue Licht zurück oder None."""
    for o in scene.objects:
        if o.type == "LIGHT" and not o.hide_render:
            return None
    obj = bpy.data.objects.get(LIGHT_OBJ)
    if obj is None:
        ld = bpy.data.lights.get(LIGHT_OBJ) or bpy.data.lights.new(LIGHT_OBJ, "SUN")
        ld.energy = 4.0
        ld.angle = 0.2
        obj = bpy.data.objects.new(LIGHT_OBJ, ld)
        coll.objects.link(obj)
    from mathutils import Euler
    obj.rotation_euler = Euler((0.75, 0.0, -0.6))        # von vorne-oben-rechts
    obj.location = (size, -size, size)
    obj.hide_render = False
    try:
        obj.hide_set(False)
    except Exception:
        pass
    return obj


def _cache_dir():
    if bpy.data.filepath:
        d = bpy.path.abspath("//" + CACHE_DIR)
        try:
            os.makedirs(d, exist_ok=True)
            return d, True
        except OSError:
            pass
    if bpy.app.tempdir:
        return bpy.app.tempdir, False
    # Benutzerordner der Extension (nie den Add-on-Ordner selbst: der kann schreibgeschützt sein)
    return bpy.utils.extension_path_user(__package__, path="cache", create=True), False


def build_volume(vol, reduction, coll, modality="CT"):
    """VDB schreiben und Volume-Objekt anlegen/ersetzen. Gibt (obj, info, persistent) zurück."""
    import openvdb          # in Blender seit 2.83 enthalten

    f = max(1, int(reduction))
    data = isosurface.pool(vol.data, f)
    if modality == "CT":
        cut = -950.0        # Luft -> Hintergrund (spart Speicher, macht das Grid dünn besetzt)
    else:
        cut = float(np.percentile(data[::2, ::4, ::4], 5))
    arr = np.ascontiguousarray(np.maximum(data, cut).transpose(2, 1, 0))   # [z,y,x] -> [x,y,z]
    del data
    grid = openvdb.FloatGrid(cut)
    grid.copyFromArray(arr, tolerance=0.0)
    del arr
    grid.name = "density"
    A = vol.affine.copy()
    if f > 1:
        P = np.diag([f, f, f, 1.0])
        P[:3, 3] = (f - 1) / 2.0
        A = A @ P
    grid.transform = openvdb.createLinearTransform(A.T.tolist())   # OpenVDB: Zeilenvektoren

    d, persistent = _cache_dir()
    path = os.path.join(d, f"RIV_volume_{int(time.time() * 1000)}.vdb")
    openvdb.write(path, grids=[grid])
    n_active = grid.activeVoxelCount()

    old = bpy.data.objects.get(VOLUME_OBJ)
    old_path = None
    if old is not None:
        if old.type == "VOLUME":
            old_path = bpy.path.abspath(old.data.filepath)
        old_data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if old_data is not None and old_data.users == 0:
            bpy.data.volumes.remove(old_data)
    if old_path and os.path.basename(old_path).startswith("RIV_volume_") and os.path.isfile(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass

    vdata = bpy.data.volumes.new(VOLUME_OBJ)
    vdata.filepath = bpy.path.relpath(path) if persistent else path
    try:
        vdata.grids.load()
    except Exception:
        pass
    vdata.materials.append(volume_material())
    obj = bpy.data.objects.new(VOLUME_OBJ, vdata)
    coll.objects.link(obj)
    info = f"Volume: {n_active:,} voxels, {os.path.getsize(path) / 1e6:.0f} MB"
    return obj, info, persistent


def setup_eevee_volumes(scene, lit=False):
    ee = getattr(scene, "eevee", None)
    if ee is None:
        return
    for attr, val in (("volumetric_tile_size", "2"), ("volumetric_samples", 96),
                      ("use_volume_custom_range", False), ("use_volumetric_shadows", bool(lit))):
        try:
            setattr(ee, attr, val)
        except Exception:
            pass


# ----------------------------------------------------------------------------------------
# Umschalten
# ----------------------------------------------------------------------------------------

def apply_display_mode(st):
    mode = st.display_mode
    vol_obj = bpy.data.objects.get(VOLUME_OBJ)
    meshes = riv_meshes()
    if mode == "GLASS":
        gm = glass_material()
        update_glass(st)
    for o in meshes:
        me = o.data
        if not me.materials:
            continue
        cur = me.materials[0]
        if mode == "GLASS":
            if cur is not None and cur.name != GLASS_MAT:
                o["riv_mat"] = cur.name
            me.materials[0] = gm
        else:
            orig = bpy.data.materials.get(o.get("riv_mat", ""))
            if orig is not None and cur is not None and cur.name == GLASS_MAT:
                me.materials[0] = orig
        _set_visible(o, mode != "VOLUME")
    if vol_obj is not None:
        _set_visible(vol_obj, mode == "VOLUME")

# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender-Oberfläche: Eigenschaften, Operatoren und Panel (3D-Ansicht > Seitenleiste > RIV)."""
import os

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)

from . import dicom_core as core
from . import image2d
from . import isosurface
from . import render_modes as rm

COLLECTION_NAME = "RIV"
AXES = ("AXIAL", "CORONAL", "SAGITTAL")
PLANE_NAMES = {"AXIAL": "RIV_Axial", "CORONAL": "RIV_Coronal", "SAGITTAL": "RIV_Sagittal"}
SUBSURF_WARN_VERTS = 500_000


def _manifest_info():
    """Name, Version, Maintainer und Website aus blender_manifest.toml (nur lesen)."""
    info = {"name": "Radiological Image Visualizer", "version": "", "maintainer": "", "website": ""}
    try:
        import tomllib
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as fh:
            data = tomllib.load(fh)
        for k in info:
            info[k] = str(data.get(k, info[k]))
    except Exception:
        pass
    return info


ADDON_INFO = _manifest_info()
REPO_URL = "https://github.com/smice-art/Radiological-Image-Visualizer"
ISSUES_URL = REPO_URL + "/issues"

# Kameras 1-3 schauen entlang X, Y, Z: (Name, Blickrichtung, Oben, Beschriftung)
# Patientenraum: X = links, Y = hinten, Z = Kopf (siehe RIV_OT_load)
CAMERAS = (
    ("RIV_Cam_1_X", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "X: side view from the patient's left (sagittal)"),
    ("RIV_Cam_2_Y", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), "Y: front view, face to the camera (coronal)"),
    ("RIV_Cam_3_Z", (0.0, 0.0, -1.0), (0.0, -1.0, 0.0), "Z: top view from the head, face up (axial)"),
)

# Laufzeit-Zustand (nicht in der .blend gespeichert)
_STATE = {"series": [], "volume": None, "busy": False, "scan_info": "", "vol_info": "",
          "range": None, "decoders": None, "images2d": []}


# ----------------------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------------------

def _get_collection(context):
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION_NAME)
    if coll.name not in context.scene.collection.children:
        context.scene.collection.children.link(coll)
    return coll


def _ensure_object_mode(context):
    """Objekte im Edit-/Sculpt-Modus zu löschen kann Blender abstürzen lassen."""
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is not None and obj.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


def _rendering():
    try:
        return bpy.app.is_job_running("RENDER")
    except Exception:
        return False


def _slice_index(vol, axis, frac):
    n = {"AXIAL": vol.data.shape[0], "CORONAL": vol.data.shape[1], "SAGITTAL": vol.data.shape[2]}[axis]
    return int(round(min(max(frac, 0.0), 1.0) * (n - 1)))


def _slice_2d(vol, axis, idx):
    if axis == "AXIAL":
        return vol.data[idx]            # (ny, nx)
    if axis == "CORONAL":
        return vol.data[:, idx, :]      # (nz, nx)
    return vol.data[:, :, idx]          # (nz, ny)


def _plane_corners(vol, axis, idx):
    nz, ny, nx = vol.data.shape
    h = 0.5
    if axis == "AXIAL":
        pts = [(-h, -h, idx), (nx - h, -h, idx), (nx - h, ny - h, idx), (-h, ny - h, idx)]
    elif axis == "CORONAL":
        pts = [(-h, idx, -h), (nx - h, idx, -h), (nx - h, idx, nz - h), (-h, idx, nz - h)]
    else:
        pts = [(idx, -h, -h), (idx, ny - h, -h), (idx, ny - h, nz - h), (idx, -h, nz - h)]
    P = np.array(pts, dtype=float)
    A = vol.affine
    return ((A[:3, :3] @ P.T).T + A[:3, 3]).astype(np.float32)


def _ensure_image(name, w, h):
    img = bpy.data.images.get(name)
    if img is not None and (img.size[0], img.size[1]) != (w, h):
        # Größe ändern statt löschen: ein gelöschtes Bild, das gerade gezeichnet wird,
        # kann den Viewport zum Absturz bringen
        try:
            img.scale(w, h)
        except Exception:
            bpy.data.images.remove(img)
            img = None
    if img is None:
        img = bpy.data.images.new(name, w, h, alpha=False, float_buffer=False)
        img.colorspace_settings.name = "Non-Color"
    return img


def _write_image(img, gray):
    h, w = gray.shape
    rgba = np.empty((h, w, 4), np.float32)
    rgba[..., 0] = gray
    rgba[..., 1] = gray
    rgba[..., 2] = gray
    rgba[..., 3] = 1.0
    img.pixels.foreach_set(rgba.ravel())
    img.update()


def _tone(g, contrast, brightness, invert):
    """Kontrast (S-Kurve), Helligkeit (Gamma) und Invertieren auf 0..1-Werte."""
    if abs(brightness) > 1e-3:
        np.power(g, 2.0 ** (-1.5 * brightness), out=g)
    if abs(contrast) > 1e-3:
        k = 2.0 ** (2.0 * contrast)
        a = np.power(g, k)
        b = np.power(1.0 - g, k)
        g = a / np.maximum(a + b, 1e-12)
    if invert:
        g = 1.0 - g
    return g.astype(np.float32, copy=False)


def _plane_material(name, img):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True          # ab 5.0 veraltet/ohne Wirkung, davor nötig
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.extension = "EXTEND"
    emi = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex.location = (-400, 0)
    emi.location = (-150, 0)
    out.location = (100, 0)
    nt.links.new(tex.outputs["Color"], emi.inputs["Color"])
    nt.links.new(emi.outputs["Emission"], out.inputs["Surface"])
    return mat


def _new_plane_object(name, coll, mat):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    uv.data.foreach_set("uv", [0, 0, 1, 0, 1, 1, 0, 1])
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    return obj


def refresh_planes(context=None, axes=AXES):
    """Bilder und Positionen der Schichtebenen aktualisieren (nur die angegebenen Achsen)."""
    vol = _STATE["volume"]
    if vol is None or _STATE["busy"] or _rendering():
        return
    scene = context.scene if context else bpy.context.scene
    st = scene.riv
    fracs = {"AXIAL": st.slice_axial, "CORONAL": st.slice_coronal, "SAGITTAL": st.slice_sagittal}
    for axis in axes:
        obj = bpy.data.objects.get(PLANE_NAMES[axis])
        if obj is None or obj.type != "MESH" or len(obj.data.vertices) != 4:
            continue
        idx = _slice_index(vol, axis, fracs[axis])
        s2d = _slice_2d(vol, axis, idx)
        img = _ensure_image(PLANE_NAMES[axis] + "_img", s2d.shape[1], s2d.shape[0])
        g = core.apply_window(s2d, st.window_center, st.window_width)
        _write_image(img, _tone(g, st.contrast, st.brightness, st.invert))
        mat = obj.data.materials[0] if obj.data.materials else None
        if mat and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.bl_idname == "ShaderNodeTexImage" and node.image != img:
                    node.image = img
        obj.data.vertices.foreach_set("co", _plane_corners(vol, axis, idx).ravel())
        obj.data.update()


def _on_view_change(self, context):
    refresh_planes(context)


def _on_axial(self, context):
    refresh_planes(context, ("AXIAL",))


def _on_coronal(self, context):
    refresh_planes(context, ("CORONAL",))


def _on_sagittal(self, context):
    refresh_planes(context, ("SAGITTAL",))


_WINDOW_PRESETS = {
    "SOFT": (40.0, 400.0), "HEART": (150.0, 700.0), "LUNG": (-600.0, 1500.0),
    "BONE": (400.0, 1800.0), "BRAIN": (40.0, 80.0),
}


def _on_window_value(self, context):
    if not _STATE["busy"] and self.view_preset != "CUSTOM":
        _STATE["busy"] = True          # verhindert Rekursion über _on_view_preset
        try:
            self.view_preset = "CUSTOM"
        finally:
            _STATE["busy"] = False
    refresh_planes(context)


def _on_view_preset(self, context):
    if _STATE["busy"] or self.view_preset not in _WINDOW_PRESETS:
        return
    _STATE["busy"] = True
    try:
        self.window_center, self.window_width = _WINDOW_PRESETS[self.view_preset]
    finally:
        _STATE["busy"] = False
    refresh_planes(context)


_PRESETS = {"BONE": 300.0, "SKIN": -300.0, "VESSEL": 200.0}


def _in_data_range(value):
    rng = _STATE.get("range")
    return rng is None or rng[0] < value < rng[1]


def _on_preset(self, context):
    if self.mesh_preset in _PRESETS:
        self.threshold = _PRESETS[self.mesh_preset]
    elif self.mesh_preset == "CUSTOM" and _STATE["volume"] is not None and not _in_data_range(self.threshold):
        # Ein von einem anderen Preset übrig gebliebener Wert (z. B. -300 HU bei einem MRT)
        # liegt außerhalb der Daten -> sinnvollen Startwert vorschlagen
        self.threshold = round(isosurface.suggest_threshold(_STATE["volume"].data), 1)


# ----------------------------------------------------------------------------------------
# Kameras
# ----------------------------------------------------------------------------------------

def _volume_corners(vol):
    nz, ny, nx = vol.data.shape
    c = np.array([[i, j, k, 1.0] for i in (-0.5, nx - 0.5) for j in (-0.5, ny - 0.5) for k in (-0.5, nz - 0.5)])
    return (vol.affine @ c.T).T[:, :3]


def _scan_rotation(vol):
    """Rotation, die die Patientenachsen X/Y/Z auf die jeweils nächstliegende Achse des
    Schichtgitters dreht (z. B. bei schräg geführten MRT-Schichten). Identität, wenn das
    Gitter nicht eindeutig zuzuordnen ist."""
    from itertools import permutations
    A = np.asarray(vol.affine[:3, :3], float)
    e0 = A[:, 0] / (np.linalg.norm(A[:, 0]) or 1.0)
    e1 = A[:, 1] - e0 * (e0 @ A[:, 1])
    e1 /= (np.linalg.norm(e1) or 1.0)
    e2 = np.cross(e0, e1)
    B = np.stack([e0, e1, e2], axis=1)                 # orthonormale Gitterachsen (Spalten)
    best, best_perm = -1.0, None
    for perm in permutations(range(3)):                # Patientenachse p <- Gitterachse perm[p]
        score = sum(abs(B[p, perm[p]]) for p in range(3))
        if score > best:
            best, best_perm = score, perm
    R = np.stack([B[:, best_perm[p]] * np.sign(B[p, best_perm[p]] or 1.0) for p in range(3)], axis=1)
    if np.linalg.det(R) < 0.5:
        return np.eye(3)
    return R


def _create_cameras(context, vol, coll):
    from mathutils import Matrix, Vector
    st = context.scene.riv
    corners = _volume_corners(vol)
    center = (corners.min(axis=0) + corners.max(axis=0)) / 2.0
    size = float(np.ptp(corners, axis=0).max()) or 1.0
    R = _scan_rotation(vol) if st.cam_align == "SCAN" else np.eye(3)
    lens = 50.0
    half_fov = np.arctan(18.0 / lens)                     # 36-mm-Sensor
    for idx, (name, fwd, up, _label) in enumerate(CAMERAS):
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        cam = bpy.data.cameras.get(name)
        if cam is None or cam.users:
            cam = bpy.data.cameras.new(name)
        f_np = R @ np.asarray(fwd, float)
        u_np = R @ np.asarray(up, float)
        r_np = np.cross(f_np, u_np)
        # Ausdehnung im Blickfeld der Kamera: Ecken auf die Kameraachsen projizieren
        rel = corners - center
        view_w = max(float(np.ptp(rel @ r_np)), float(np.ptp(rel @ u_np)))
        depth = float(np.ptp(rel @ f_np))
        f = Vector(f_np)
        u = Vector(u_np)
        dist = (view_w / 2.0) / np.tan(half_fov) * 1.25 + depth / 2.0
        cam.lens = lens
        cam.ortho_scale = view_w * 1.15
        cam.type = "ORTHO" if st.cam_ortho else "PERSP"
        cam.clip_start = size * 0.005
        cam.clip_end = dist + size * 10.0
        obj = bpy.data.objects.new(name, cam)
        z_axis = -f
        x_axis = u.cross(z_axis)
        rot = Matrix((x_axis, u, z_axis)).transposed()
        obj.matrix_world = Matrix.Translation(Vector(center) - f * dist) @ rot.to_4x4()
        coll.objects.link(obj)
    if context.scene.camera is None:
        _set_scene_camera(context, st.cam_index, enter_view=False)


def _set_scene_camera(context, index, enter_view=True):
    name = CAMERAS[max(1, min(3, index)) - 1][0]
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    context.scene.camera = obj
    if enter_view:
        space = getattr(context, "space_data", None)
        spaces = [space] if space is not None and space.type == "VIEW_3D" else []
        if not spaces and context.screen:
            spaces = [a.spaces.active for a in context.screen.areas if a.type == "VIEW_3D"][:1]
        for sp in spaces:
            try:
                sp.region_3d.view_perspective = "CAMERA"
            except Exception:
                pass
    return True


def _on_cam_index(self, context):
    _set_scene_camera(context, self.cam_index)


def _on_cam_align(self, context):
    vol = _STATE["volume"]
    if vol is None or not any(bpy.data.objects.get(c[0]) for c in CAMERAS):
        return
    was_dicom = context.scene.camera is not None and context.scene.camera.name in [c[0] for c in CAMERAS]
    _create_cameras(context, vol, _get_collection(context))
    if was_dicom:
        _set_scene_camera(context, self.cam_index, enter_view=False)


def _on_cam_ortho(self, context):
    for name, *_rest in CAMERAS:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "CAMERA":
            obj.data.type = "ORTHO" if self.cam_ortho else "PERSP"


def _on_display_mode(self, context):
    rm.apply_display_mode(self)
    if self.display_mode in ("GLASS", "VOLUME"):
        space = getattr(context, "space_data", None)
        if space and space.type == "VIEW_3D" and space.shading.type in ("SOLID", "WIREFRAME"):
            space.shading.type = "MATERIAL"
    if self.display_mode == "VOLUME" and bpy.data.objects.get(rm.VOLUME_OBJ) is None \
            and _STATE["volume"] is not None:
        # Aufbau nach dem Update-Callback ausführen (dort sind Operatoren tabu)
        bpy.app.timers.register(_deferred_build_volume, first_interval=0.05)


def _deferred_build_volume():
    try:
        bpy.ops.riv.build_volume("EXEC_DEFAULT")
    except Exception as e:
        print("RIV: volume build failed:", e)
    return None


def _on_glass(self, context):
    rm.update_glass(self)


def _on_vr(self, context):
    rm.update_volume(self)


def _on_vr_preset(self, context):
    if self.vr_preset in rm.VR_PRESETS:
        p = rm.VR_PRESETS[self.vr_preset]
        _STATE["busy"] = True
        try:
            self.vr_low, self.vr_high = p["low"], p["high"]
        finally:
            _STATE["busy"] = False
        rm.set_ramp(self.vr_preset)
    rm.update_volume(self)


def _apply_vr_shading(st, scene, context=None):
    rm.set_shading(st.vr_shading)
    rm.setup_eevee_volumes(scene, lit=st.vr_shading == "LIT")
    if st.vr_shading == "LIT" and bpy.data.objects.get(rm.VOLUME_OBJ) is not None:
        vol = _STATE["volume"]
        size = 0.2
        if vol is not None:
            size = float(np.ptp(_volume_corners(vol), axis=0).max())
        ctx = context or bpy.context
        if rm.ensure_light(scene, _get_collection(ctx), size) is not None:
            _STATE["vol_info"] = "Lit mode: added a sun light (RIV_Light)"


def _on_vr_shading(self, context):
    _apply_vr_shading(self, context.scene, context)


def _on_2d_window(self, context):
    obj = self.id_data
    image2d.update_window(obj)


def _clear_scene_objects():
    for name in list(PLANE_NAMES.values()) + [rm.VOLUME_OBJ, rm.LIGHT_OBJ] + [c[0] for c in CAMERAS]:
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    for obj in list(bpy.data.objects):
        if obj.get("riv_mesh") or obj.get(image2d.PROP):
            bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.images, bpy.data.materials, bpy.data.volumes, bpy.data.cameras,
                 bpy.data.lights):
        for item in list(coll):
            if item.name.startswith("RIV_") and item.users == 0:
                coll.remove(item)


def _build_mesh(name, verts, quads):
    nv, nf = len(verts), len(quads)
    mesh = bpy.data.meshes.new(name)
    try:
        mesh.vertices.add(nv)
        mesh.vertices.foreach_set("co", verts.astype(np.float32).ravel())
        mesh.loops.add(nf * 4)
        mesh.loops.foreach_set("vertex_index", quads.astype(np.int32).ravel())
        mesh.polygons.add(nf)
        mesh.polygons.foreach_set("loop_start", np.arange(0, nf * 4, 4, dtype=np.int32))
        try:
            mesh.polygons.foreach_set("loop_total", np.full(nf, 4, dtype=np.int32))
        except Exception:
            pass   # ab Blender 4.x aus den Offsets abgeleitet
        mesh.update(calc_edges=True)
        if len(mesh.polygons) != nf or len(mesh.vertices) != nv:
            raise RuntimeError("unexpected mesh size")
    except Exception:
        bpy.data.meshes.remove(mesh)
        if nv > 300_000:
            # Der Python-Listen-Fallback braucht ein Vielfaches an Speicher und kann
            # bei großen Meshes Blender zum Absturz bringen -> lieber sauber abbrechen
            raise MemoryError("mesh too large for the fallback path")
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts.tolist(), [], quads.tolist())
        mesh.update()
    try:
        mesh.shade_smooth()
    except Exception:
        try:
            mesh.polygons.foreach_set("use_smooth", np.ones(nf, dtype=bool))
        except Exception:
            pass
    return mesh


def _mesh_material(preset):
    name = f"RIV_Mat_{preset}"
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        return mat
    if preset == "SKIN":
        bsdf.inputs["Base Color"].default_value = (0.85, 0.58, 0.47, 1.0)
        bsdf.inputs["Alpha"].default_value = 0.35
        bsdf.inputs["Roughness"].default_value = 0.5
        # DITHERED statt BLENDED: kein Sortieren der Millionen Flächen pro Frame (GPU-Last)
        try:
            mat.surface_render_method = "DITHERED"
        except Exception:
            pass
    elif preset == "BONE":
        bsdf.inputs["Base Color"].default_value = (0.93, 0.88, 0.76, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.45
    elif preset == "VESSEL":
        bsdf.inputs["Base Color"].default_value = (0.72, 0.12, 0.1, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.35
        try:
            bsdf.inputs["Coat Weight"].default_value = 0.3
        except Exception:
            pass
    else:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.78, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


# ----------------------------------------------------------------------------------------
# Eigenschaften
# ----------------------------------------------------------------------------------------

class RIV_Series(bpy.types.PropertyGroup):
    label: StringProperty()
    index: IntProperty()      # Index in _STATE["series"]


class RIV_Image2D(bpy.types.PropertyGroup):
    label: StringProperty()
    index: IntProperty()      # Index in _STATE["images2d"]


class RIV_2DPlane(bpy.types.PropertyGroup):
    """Window/Level einer 2D-Bildebene (am Objekt gespeichert)."""
    level: FloatProperty(
        name="Level", default=0.0, update=_on_2d_window,
        description="Centre of the displayed value range of this 2D image. Live")
    window: FloatProperty(
        name="Window", default=1000.0, min=1e-3, update=_on_2d_window,
        description="Width of the displayed value range. Smaller = more contrast. Live")
    invert: BoolProperty(
        name="Invert", default=False, update=_on_2d_window,
        description="Show the image inverted (X-rays: bone dark instead of bright)")


class RIV_Settings(bpy.types.PropertyGroup):
    directory: StringProperty(
        name="Folder", subtype="DIR_PATH",
        description="Folder containing the DICOM files (subfolders are searched too). "
                    "Note: this path is stored in the .blend file and may contain names")
    recursive: BoolProperty(
        name="Subfolders", default=True,
        description="Also search all subfolders")
    series: CollectionProperty(type=RIV_Series)
    series_index: IntProperty()
    images2d: CollectionProperty(type=RIV_Image2D)
    images2d_index: IntProperty()

    downsample: IntProperty(
        name="Load Reduction", min=1, max=4, default=1,
        description="Shrinks the volume while loading, by this factor in every direction "
                    "(2 = 1/8 of the voxels, 3 = 1/27, 4 = 1/64). Saves memory and speeds up everything, "
                    "but affects slice images AND mesh. Use 1 for full detail. Independent of 'Mesh Reduction'")
    unit_scale: FloatProperty(
        name="Scale", default=0.001, min=1e-6, precision=4,
        description="Blender units per millimetre. 0.001 = real-world size when the scene unit is metres "
                    "(1 mm stays 1 mm). 0.01 = 10x larger, 1 = 1000x larger. Takes effect on the next load")
    unit_scale_loaded: FloatProperty(default=0.001, options={"HIDDEN"})

    # --- Slices ---
    view_preset: EnumProperty(
        name="Preset", default="SOFT", update=_on_view_preset,
        description="Typical Level/Window combinations (CT values)",
        items=[("SOFT", "Soft Tissue", "Level 40, Window 400"),
               ("HEART", "Heart / Vessels", "Level 150, Window 700: contrast-filled blood and heart muscle"),
               ("LUNG", "Lung", "Level -600, Window 1500"),
               ("BONE", "Bone", "Level 400, Window 1800"),
               ("BRAIN", "Brain", "Level 40, Window 80"),
               ("CUSTOM", "Custom", "Your own Level/Window")])
    window_center: FloatProperty(
        name="Level", default=40.0, update=_on_window_value,
        description="Centre of the displayed value range (HU for CT). Moves WHICH tissue range is visible. "
                    "Affects the slice images only, not the mesh")
    window_width: FloatProperty(
        name="Window", default=400.0, min=1.0, update=_on_window_value,
        description="Width of the displayed value range. Small = high contrast in a narrow range, "
                    "large = flatter image covering a wider range. Affects the slice images only")
    contrast: FloatProperty(
        name="Contrast", default=0.0, min=-1.0, max=1.0, subtype="FACTOR", update=_on_view_change,
        description="S-curve on top of Level/Window. Positive = more contrast in the mid-tones "
                    "(edges pop), negative = softer. 0 = linear")
    brightness: FloatProperty(
        name="Brightness", default=0.0, min=-1.0, max=1.0, subtype="FACTOR", update=_on_view_change,
        description="Brightens (positive) or darkens (negative) the mid-tones without clipping "
                    "black or white (gamma). 0 = unchanged")
    invert: BoolProperty(
        name="Invert", default=False, update=_on_view_change,
        description="Show the slice images inverted (bright = low values)")
    slice_axial: FloatProperty(
        name="Axial", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_axial,
        description="Position of the axial plane (horizontal cut): 0 = first slice (usually the feet), "
                    "1 = last slice (usually the head)")
    slice_coronal: FloatProperty(
        name="Coronal", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_coronal,
        description="Position of the coronal plane (front-to-back cut): 0 = front (usually belly/face), "
                    "1 = back. Directions assume a standard supine scan")
    slice_sagittal: FloatProperty(
        name="Sagittal", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_sagittal,
        description="Position of the sagittal plane (left-to-right cut): 0 = patient's right side, "
                    "1 = patient's left side. Directions assume a standard supine scan")

    # --- Mesh ---
    mesh_preset: EnumProperty(
        name="Tissue", update=_on_preset, default="BONE",
        description="Quick presets for the threshold and the material of the new mesh",
        items=[("BONE", "Bone (CT)", "Threshold about 300 HU: bone including spongy bone"),
               ("VESSEL", "Heart / Vessels (CT)",
                "Threshold about 200 HU: contrast-filled heart chambers and vessels (CT angiography)"),
               ("SKIN", "Skin (CT)", "Threshold about -300 HU: body surface (air/skin boundary), semi-transparent"),
               ("CUSTOM", "Custom", "Free threshold, e.g. for MRI")])
    threshold: FloatProperty(
        name="Threshold", default=300.0,
        description="The surface is built where values rise above this level (HU for CT). "
                    "It selects WHICH tissue forms the surface, not how detailed it is: "
                    "-300 = skin, 150-250 = contrast vessels, 300 = bone, 500+ = only dense bone. "
                    "Avoid values in the middle of a tissue's own range (e.g. 100 HU for heart muscle): "
                    "the surface then follows the noise. Press 'Create Mesh' again after changing it")
    mesh_step: IntProperty(
        name="Mesh Reduction", min=1, max=4, default=2,
        description="Mesh detail: 1 = full resolution (very many polygons), 2 = about 1/4 of the polygons, "
                    "3 = about 1/9, 4 = about 1/16. Independent of 'Load Reduction'")
    close_ends: BoolProperty(
        name="Close Ends", default=True,
        description="Cap the surface where the volume ends, so the object looks solid. "
                    "Off = open at the cut faces, you can look inside")
    smooth_mm: FloatProperty(
        name="Noise Filter", default=0.8, min=0.0, max=4.0, step=10, precision=1, unit="NONE",
        description="Removes scanner noise from the volume BEFORE the surface is built (Gaussian, in mm). "
                    "This is what makes the mesh smooth: about 0.5-1 mm keeps small vessels, "
                    "1.5-2 mm gives very smooth organ shapes. 0 = off (raw, grainy surface)")
    edge_preserve: BoolProperty(
        name="Edge-Preserving", default=False,
        description="Use an edge-preserving filter (anisotropic diffusion) first: smooths noise inside "
                    "tissues but keeps boundaries sharp, so thin vessels survive. Slower (about 5-15 s)")
    relax_iter: IntProperty(
        name="Relax", default=10, min=0, max=50,
        description="Smooths the remaining voxel steps on the mesh itself (Taubin, no shrinking). "
                    "Each vertex stays inside its voxel, so no detail is invented or lost. 0 = off")
    island_pct: FloatProperty(
        name="Remove Islands", default=1.0, min=0.0, max=50.0, precision=1, subtype="PERCENTAGE",
        description="Deletes loose parts smaller than this percentage of the largest part "
                    "(noise specks, table fragments). 0 = keep everything")
    set_standard_view: BoolProperty(
        name="Set Color Management to 'Standard'", default=True,
        description="Sets the scene's view transform to 'Standard' so the slice images show unaltered grey values "
                    "(AgX/Filmic would change them)")

    # --- Display ---
    display_mode: EnumProperty(
        name="Display", default="SURFACE", update=_on_display_mode,
        description="How the anatomy is shown. Switch freely; nothing is lost",
        items=[("SURFACE", "Surface", "Solid meshes with their normal materials", "MESH_ICOSPHERE", 0),
               ("GLASS", "Glass", "Meshes as see-through X-ray glass (fast, order-independent)", "XRAY", 1),
               ("VOLUME", "Volume", "True volume rendering of the scan data (semi-transparent, no mesh needed)",
                "VOLUME_DATA", 2)])
    glass_color: FloatVectorProperty(
        name="Color", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1.0, 0.45, 0.3), update=_on_glass)
    glass_brightness: FloatProperty(
        name="Brightness", default=1.0, min=0.0, max=10.0, update=_on_glass,
        description="Glow strength of the glass outlines")
    glass_edge: FloatProperty(
        name="Edge Focus", default=2.0, min=0.2, max=8.0, update=_on_glass,
        description="Higher = only silhouettes glow, lower = surfaces facing you glow too")
    glass_core: FloatProperty(
        name="Core", default=0.05, min=0.0, max=1.0, subtype="FACTOR", update=_on_glass,
        description="Minimum glow of surfaces facing the camera")
    vr_preset: EnumProperty(
        name="Transfer", default="HEART", update=_on_vr_preset,
        description="Which values become visible and in which colours",
        items=[("HEART", "Heart / Vessels", "Contrast blood red, bone white, heart muscle hidden"),
               ("VESSELS", "Vessels (no bone)", "Only contrast-filled vessels and chambers: muscle AND bone hidden "
                                                "(two-sided opacity curve)"),
               ("BONE", "Bone", "Only bone, ivory"),
               ("SOFT", "Soft Tissue", "Skin and muscle, translucent"),
               ("XRAY", "X-Ray", "Grey ramp over a wide range")])
    vr_shading: EnumProperty(
        name="Shading", default="GLOW", update=_on_vr_shading,
        description="How the volume is lit",
        items=[("GLOW", "Glow", "Self-illuminated (emission + absorption): independent of lights, fast, "
                                "noise-free. The classic medical volume rendering look"),
               ("LIT", "Lit", "Lit by lamps/HDRI with light scattering and soft shadows inside the tissue "
                              "(Principled Volume): more photo-real, slower. Adds a sun if the scene has no light")])
    vr_anisotropy: FloatProperty(
        name="Anisotropy", default=0.3, min=-0.95, max=0.95, update=_on_vr,
        description="Lit mode: direction of light scattering. Positive = light passes forward through the "
                    "tissue (glowing rim against the light), negative = scattered back, 0 = even")
    vr_glow: FloatProperty(
        name="Self-Glow", default=0.15, min=0.0, max=2.0, update=_on_vr,
        description="Lit mode: how much self-illumination is added on top of the lighting "
                    "(keeps dark sides readable). 0 = light only")
    vr_low: FloatProperty(
        name="Low", default=100.0, update=_on_vr,
        description="Values below this are fully transparent (HU for CT). Live")
    vr_high: FloatProperty(
        name="High", default=700.0, update=_on_vr,
        description="Values above this are fully opaque and use the last colour. Live")
    vr_opacity: FloatProperty(
        name="Opacity", default=1.0, min=0.0, max=20.0, update=_on_vr,
        description="Overall density. Lower = more see-through. Live")
    vr_brightness: FloatProperty(
        name="Brightness", default=1.0, min=0.0, max=10.0, update=_on_vr,
        description="Glow strength of the volume. Live")
    cam_index: IntProperty(
        name="Camera", default=1, min=1, max=3, update=_on_cam_index,
        description="Switch between the three cameras (use the arrows): "
                    "1 = along X (side), 2 = along Y (front), 3 = along Z (top). "
                    "Makes it the scene camera and looks through it")
    cam_ortho: BoolProperty(
        name="Orthographic", default=True, update=_on_cam_ortho,
        description="Orthographic = no perspective distortion, like the slice images (good for comparing "
                    "sizes). Off = normal perspective camera")
    cam_align: EnumProperty(
        name="Align", default="SCAN", update=_on_cam_align,
        description="Which axes the cameras follow",
        items=[("SCAN", "Scan Axes", "Look straight along the slice grid (perpendicular to the slices). "
                                     "Best for tilted MRI slabs; matches the slice planes"),
               ("PATIENT", "Patient Axes", "Look exactly along the body axes X/Y/Z (Blender world axes)")])
    vr_reduction: IntProperty(
        name="Volume Reduction", default=2, min=1, max=4,
        description="Resolution of the volume object: 1 = full (best for Cycles, large file), "
                    "2 = 1/8 of the voxels (recommended for EEVEE)")


# ----------------------------------------------------------------------------------------
# Operatoren
# ----------------------------------------------------------------------------------------

class RIV_OT_scan(bpy.types.Operator):
    bl_idname = "riv.scan"
    bl_label = "Scan Folder"
    bl_description = "Search the folder for DICOM series (reads only the headers, no image data)"

    def execute(self, context):
        st = context.scene.riv
        if not st.directory:
            self.report({"ERROR"}, "Please choose a folder first.")
            return {"CANCELLED"}
        path = bpy.path.abspath(st.directory)
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        try:
            res = core.scan_directory(path, st.recursive, progress=lambda n: wm.progress_update(min(n, 999)))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        _STATE["series"] = res.series
        _STATE["images2d"] = res.images2d
        _STATE["decoders"] = core.decoder_status()
        st.series.clear()
        for i, s in enumerate(res.series):
            item = st.series.add()
            item.label = s.label()
            item.index = i
        st.series_index = 0
        st.images2d.clear()
        for i, im in enumerate(res.images2d):
            item = st.images2d.add()
            item.label = im.label()
            item.index = i
        st.images2d_index = 0
        info = f"{len(res.series)} 3D series"
        if res.images2d:
            info += f", {len(res.images2d)} 2D image(s)"
        info += f" in {res.n_files} files"
        if res.n_multiframe:
            info += f"; {res.n_multiframe} multi-frame file(s) not supported yet"
        _STATE["scan_info"] = info
        if not res.series and res.images2d:
            mods = sorted({im.modality for im in res.images2d})
            self.report({"INFO"}, f"No 3D series, but {len(res.images2d)} single 2D image(s) "
                                  f"({', '.join(mods)}) - see section '5. 2D Images'")
            return {"FINISHED"}
        if not res.series:
            self.report({"WARNING"}, "No DICOM images found. " + info)
            return {"FINISHED"}
        self.report({"INFO"}, info)
        return {"FINISHED"}


class RIV_OT_load(bpy.types.Operator):
    bl_idname = "riv.load"
    bl_label = "Load Series"
    bl_description = "Read the selected series and create three slice planes in 3D space"

    def execute(self, context):
        st = context.scene.riv
        if not (0 <= st.series_index < len(st.series)) or not _STATE["series"]:
            self.report({"ERROR"}, "No series selected - scan the folder again.")
            return {"CANCELLED"}
        info = _STATE["series"][st.series[st.series_index].index]
        _ensure_object_mode(context)
        wm = context.window_manager
        wm.progress_begin(0, len(info.files))
        try:
            vol = core.load_series(info, st.downsample, progress=wm.progress_update)
        except MemoryError:
            self.report({"ERROR"}, "Not enough memory - increase 'Load Reduction'.")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        # Patientenraum (LPS, mm) -> Blender: LPS passt direkt auf X=links, Y=hinten, Z=oben
        # (ein Patient, der die Frontansicht anschaut, blickt in -Y). Zentrieren + skalieren:
        nz, ny, nx = vol.data.shape
        A = vol.affine
        center = (A @ np.array([(nx - 1) / 2, (ny - 1) / 2, (nz - 1) / 2, 1.0]))[:3]
        T = np.eye(4)
        T[:3, 3] = -center
        S = np.diag([st.unit_scale] * 3 + [1.0])
        vol.affine = S @ T @ A

        _clear_scene_objects()
        _STATE["volume"] = None          # alten Speicher freigeben, bevor der neue gebunden wird
        _STATE["volume"] = vol
        _STATE["vol_info"] = ""
        step = max(1, vol.data.shape[0] // 32)
        lo_r, hi_r = np.percentile(vol.data[::step, ::4, ::4], [0.0, 100.0])
        _STATE["range"] = (float(lo_r), float(hi_r))
        _STATE["busy"] = True
        try:
            st.unit_scale_loaded = st.unit_scale
            st.window_center, st.window_width = vol.window
            st.view_preset = "SOFT" if vol.modality == "CT" else "CUSTOM"
            st.slice_axial = st.slice_coronal = st.slice_sagittal = 0.5
            st.display_mode = "SURFACE"
            if vol.modality == "CT":
                st.mesh_preset = "BONE"
                st.threshold = _PRESETS["BONE"]
            else:
                st.mesh_preset = "CUSTOM"
                st.threshold = round(isosurface.suggest_threshold(vol.data), 1)
                lo, hi = np.percentile(vol.data[::2, ::4, ::4], [60, 99.5])
                st.vr_preset = "XRAY"
                st.vr_low, st.vr_high = float(lo), float(hi)
        finally:
            _STATE["busy"] = False

        coll = _get_collection(context)
        for axis in AXES:
            img = _ensure_image(PLANE_NAMES[axis] + "_img", 4, 4)
            mat = _plane_material(PLANE_NAMES[axis] + "_mat", img)
            _new_plane_object(PLANE_NAMES[axis], coll, mat)
        try:
            _create_cameras(context, vol, coll)
        except Exception as e:
            self.report({"WARNING"}, f"Cameras could not be created: {e}")
        if st.set_standard_view:
            try:
                context.scene.view_settings.view_transform = "Standard"
            except Exception:
                pass
        refresh_planes(context)

        space = context.space_data
        if space and space.type == "VIEW_3D":
            try:
                space.shading.type = "MATERIAL"
            except Exception:
                pass
            try:
                for o in context.view_layer.objects:
                    o.select_set(o.name in PLANE_NAMES.values())
                bpy.ops.view3d.view_selected()
            except Exception:
                pass

        for w in vol.warnings:
            self.report({"WARNING"}, w)
        vs = vol.voxel_size / st.unit_scale
        self.report({"INFO"}, f"Loaded: {nx} x {ny} x {nz} voxels, {vs[0]:.2f} x {vs[1]:.2f} x {vs[2]:.2f} mm")
        return {"FINISHED"}


class RIV_OT_auto_window(bpy.types.Operator):
    bl_idname = "riv.auto_window"
    bl_label = "Auto"
    bl_description = "Set Level/Window automatically from the value distribution inside the body"

    def execute(self, context):
        vol = _STATE["volume"]
        if vol is None:
            return {"CANCELLED"}
        st = context.scene.riv
        step = max(1, vol.data.shape[0] // 24)
        s = vol.data[::step, ::3, ::3]
        floor = -500.0 if vol.modality == "CT" else float(np.percentile(s, 30))
        body = s[s > floor]
        if body.size < 100:
            body = s.ravel()
        lo, hi = np.percentile(body, [2.0, 98.0])
        if hi <= lo:
            hi = lo + 1.0
        _STATE["busy"] = True
        try:
            st.view_preset = "CUSTOM"
            st.window_center = float((lo + hi) / 2)
            st.window_width = float(hi - lo)
            st.contrast = 0.0
            st.brightness = 0.0
        finally:
            _STATE["busy"] = False
        refresh_planes(context)
        return {"FINISHED"}


class RIV_OT_reset_tone(bpy.types.Operator):
    bl_idname = "riv.reset_tone"
    bl_label = "Reset"
    bl_description = "Reset Contrast, Brightness and Invert"

    def execute(self, context):
        st = context.scene.riv
        _STATE["busy"] = True
        try:
            st.contrast = 0.0
            st.brightness = 0.0
            st.invert = False
        finally:
            _STATE["busy"] = False
        refresh_planes(context)
        return {"FINISHED"}


class RIV_OT_suggest_threshold(bpy.types.Operator):
    bl_idname = "riv.suggest_threshold"
    bl_label = "Suggest"
    bl_description = ("Suggest a threshold from the value distribution of this scan (Otsu: splits background "
                      "and tissue). Useful for MRI, where there are no fixed values like HU")

    def execute(self, context):
        vol = _STATE["volume"]
        if vol is None:
            return {"CANCELLED"}
        st = context.scene.riv
        val = round(isosurface.suggest_threshold(vol.data), 1)
        _STATE["busy"] = True
        try:
            st.mesh_preset = "CUSTOM"
        finally:
            _STATE["busy"] = False
        st.threshold = val
        self.report({"INFO"}, f"Threshold {val:g}")
        return {"FINISHED"}


class RIV_OT_cameras(bpy.types.Operator):
    bl_idname = "riv.make_cameras"
    bl_label = "Recreate Cameras"
    bl_description = "Create the three cameras (along X, Y and Z) again, framed on the volume"

    def execute(self, context):
        vol = _STATE["volume"]
        if vol is None:
            return {"CANCELLED"}
        _ensure_object_mode(context)
        _create_cameras(context, vol, _get_collection(context))
        _set_scene_camera(context, context.scene.riv.cam_index)
        return {"FINISHED"}


class RIV_OT_mesh(bpy.types.Operator):
    bl_idname = "riv.make_mesh"
    bl_label = "Create Mesh"
    bl_description = "Create a smooth surface mesh at the current threshold (adds a new object each time)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        vol = _STATE["volume"]
        if vol is None:
            self.report({"ERROR"}, "Load a series first.")
            return {"CANCELLED"}
        st = context.scene.riv
        _ensure_object_mode(context)
        step = max(1, int(st.mesh_step))
        vs_mm = vol.voxel_size / max(st.unit_scale_loaded, 1e-12)
        wm = context.window_manager
        wm.progress_begin(0, 6)
        try:
            verts, quads = isosurface.extract_surface(
                vol.data, st.threshold, st.close_ends, step,
                smooth_sigma=st.smooth_mm, voxel_size=vs_mm, relax_iter=st.relax_iter,
                island_fraction=st.island_pct / 100.0, edge_preserve=st.edge_preserve,
                progress=wm.progress_update)
        except MemoryError:
            self.report({"ERROR"}, "Not enough memory - increase 'Mesh Reduction' or 'Load Reduction'.")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Mesh creation failed: {e}")
            return {"CANCELLED"}
        finally:
            wm.progress_end()
        if len(quads) == 0:
            rng = _STATE.get("range")
            sug = isosurface.suggest_threshold(vol.data)
            if rng is not None and not (rng[0] < st.threshold < rng[1]):
                side = "below" if st.threshold <= rng[0] else "above"
                self.report({"WARNING"},
                            f"No surface: threshold {st.threshold:.0f} is {side} all values of this scan "
                            f"({rng[0]:.0f} to {rng[1]:.0f}). Try about {sug:.0f} (Suggest button).")
            else:
                self.report({"WARNING"}, f"No surface at this threshold. Try about {sug:.0f} (Suggest button).")
            return {"CANCELLED"}

        A = vol.affine
        world = verts @ A[:3, :3].T.astype(np.float32) + A[:3, 3].astype(np.float32)
        del verts
        if np.linalg.det(A[:3, :3]) < 0:
            quads = quads[:, ::-1]

        preset = st.mesh_preset
        name = f"RIV_{preset.title()}_{st.threshold:.0f}"
        try:
            mesh = _build_mesh(name, world, np.ascontiguousarray(quads))
        except MemoryError:
            self.report({"ERROR"}, "Not enough memory to build the mesh - increase 'Mesh Reduction'.")
            return {"CANCELLED"}
        mesh.materials.append(_mesh_material(preset))
        obj = bpy.data.objects.new(name, mesh)
        obj["riv_mesh"] = True
        _get_collection(context).objects.link(obj)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if st.display_mode == "VOLUME":
            st.display_mode = "SURFACE"      # sonst wäre das neue Mesh unsichtbar
        else:
            rm.apply_display_mode(st)
        self.report({"INFO"}, f"{len(world):,} vertices, {len(quads):,} faces")
        return {"FINISHED"}


class RIV_OT_build_volume(bpy.types.Operator):
    bl_idname = "riv.build_volume"
    bl_label = "Build Volume"
    bl_description = ("Create (or rebuild) the volume object for true volume rendering. "
                      "Colours and range can then be changed live")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        vol = _STATE["volume"]
        if vol is None:
            self.report({"ERROR"}, "Load a series first.")
            return {"CANCELLED"}
        st = context.scene.riv
        _ensure_object_mode(context)
        try:
            obj, info, persistent = rm.build_volume(vol, st.vr_reduction, _get_collection(context), vol.modality)
        except ImportError:
            self.report({"ERROR"}, "This Blender build has no OpenVDB Python module.")
            return {"CANCELLED"}
        except MemoryError:
            self.report({"ERROR"}, "Not enough memory - increase 'Volume Reduction'.")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Volume creation failed: {e}")
            return {"CANCELLED"}
        if st.vr_preset in rm.VR_PRESETS:
            rm.set_ramp(st.vr_preset)
        rm.update_volume(st)
        _STATE["vol_info"] = info + ("" if persistent else " (temporary: save the .blend to keep it)")
        _apply_vr_shading(st, context.scene, context)
        if st.display_mode != "VOLUME":
            _STATE["busy"] = True
            try:
                st.display_mode = "VOLUME"
            finally:
                _STATE["busy"] = False
        rm.apply_display_mode(st)
        space = getattr(context, "space_data", None)
        if space and space.type == "VIEW_3D" and space.shading.type in ("SOLID", "WIREFRAME"):
            space.shading.type = "MATERIAL"
        self.report({"INFO"}, info)
        return {"FINISHED"}


class RIV_OT_import_2d(bpy.types.Operator):
    bl_idname = "riv.import_2d"
    bl_label = "Import 2D Image"
    bl_description = ("Import the selected 2D image (e.g. an X-ray) as a picture plane in real size. "
                      "The image is packed into the .blend file")
    bl_options = {"REGISTER", "UNDO"}

    import_all: BoolProperty(name="All", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        st = context.scene.riv
        items = list(st.images2d) if self.import_all else (
            [st.images2d[st.images2d_index]] if 0 <= st.images2d_index < len(st.images2d) else [])
        if not items or not _STATE["images2d"]:
            self.report({"ERROR"}, "No 2D image selected - scan the folder again.")
            return {"CANCELLED"}
        _ensure_object_mode(context)
        coll = _get_collection(context)
        vol_max_x = None
        if _STATE["volume"] is not None:
            vol_max_x = float(_volume_corners(_STATE["volume"])[:, 0].max())
        gap = 20.0 * st.unit_scale          # 2 cm Abstand
        created, guessed = [], 0
        wm = context.window_manager
        wm.progress_begin(0, len(items))
        try:
            for k, item in enumerate(items):
                try:
                    info = _STATE["images2d"][item.index]
                except IndexError:
                    continue
                try:
                    arr, window = core.load_image2d(info)
                except MemoryError:
                    self.report({"ERROR"}, "Not enough memory for this image.")
                    break
                except Exception as e:
                    self.report({"ERROR"}, str(e))
                    continue
                x0 = image2d.next_x(gap, vol_max_x)
                obj, was_guessed = image2d.create_plane(arr, window, info, coll, st.unit_scale, x0)
                guessed += int(was_guessed)
                created.append(obj)
                wm.progress_update(k + 1)
        finally:
            wm.progress_end()
        if not created:
            return {"CANCELLED"}
        for o in context.view_layer.objects:
            o.select_set(False)
        for o in created:
            o.select_set(True)
        context.view_layer.objects.active = created[-1]
        space = getattr(context, "space_data", None)
        if space and space.type == "VIEW_3D":
            if space.shading.type in ("SOLID", "WIREFRAME"):
                space.shading.type = "MATERIAL"
            if st.set_standard_view:
                try:
                    context.scene.view_settings.view_transform = "Standard"
                except Exception:
                    pass
            try:
                bpy.ops.view3d.view_selected()
            except Exception:
                pass
        msg = f"{len(created)} image plane(s) created"
        if guessed:
            msg += f"; {guessed} without pixel size in the file (assumed 0.1 mm)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class RIV_OT_reset_2d(bpy.types.Operator):
    bl_idname = "riv.reset_2d"
    bl_label = "Reset"
    bl_description = "Reset Level/Window of the active 2D image to the values from the file"

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and bool(o.get(image2d.PROP))

    def execute(self, context):
        o = context.active_object
        c, w = o.get("riv_window_default", (o.riv_2d.level, o.riv_2d.window))
        o.riv_2d.level = float(c)
        o.riv_2d.window = float(w)
        o.riv_2d.invert = False
        return {"FINISHED"}


class RIV_OT_clear(bpy.types.Operator):
    bl_idname = "riv.clear"
    bl_label = "Remove"
    bl_description = "Free the loaded volume from memory and delete the objects created by the add-on"

    def execute(self, context):
        _ensure_object_mode(context)
        vobj = bpy.data.objects.get(rm.VOLUME_OBJ)
        vpath = bpy.path.abspath(vobj.data.filepath) if vobj is not None and vobj.type == "VOLUME" else None
        _clear_scene_objects()
        if vpath and os.path.basename(vpath).startswith("RIV_volume_") and os.path.isfile(vpath):
            try:
                os.remove(vpath)
            except OSError:
                pass
        _STATE["volume"] = None
        _STATE["vol_info"] = ""
        _STATE["range"] = None
        coll = bpy.data.collections.get(COLLECTION_NAME)
        if coll and not coll.objects:
            bpy.data.collections.remove(coll)
        return {"FINISHED"}


# ----------------------------------------------------------------------------------------
# Panel
# ----------------------------------------------------------------------------------------

class RIV_UL_series(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.label, icon="IMAGE_DATA")


class RIV_UL_images2d(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.label, icon="IMAGE_RGB")


def _heavy_subsurf_objects():
    out = []
    for o in rm.riv_meshes():
        if len(o.data.vertices) > SUBSURF_WARN_VERTS and any(
                m.type == "SUBSURF" and (m.show_viewport or m.show_render) for m in o.modifiers):
            out.append(o.name)
    return out


class _RIVPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RIV"


class RIV_PT_main(_RIVPanel, bpy.types.Panel):
    """Kopf: Status des geladenen Volumens. Die Abschnitte darunter sind einklappbare Unterpanels."""
    bl_label = "Radiological Image Visualizer"
    bl_idname = "RIV_PT_main"

    def draw(self, context):
        st = context.scene.riv
        lay = self.layout
        lay.label(text="Not a medical device - not for diagnosis.", icon="ERROR")
        vol = _STATE["volume"]
        if vol is None:
            if any(bpy.data.objects.get(n) for n in PLANE_NAMES.values()):
                lay.label(text="Volume not in memory: scan and load again", icon="INFO")
            else:
                lay.label(text="No volume loaded", icon="INFO")
        else:
            nz, ny, nx = vol.data.shape
            vs = vol.voxel_size / max(st.unit_scale_loaded, 1e-12)
            col = lay.column(align=True)
            col.label(text=f"{nx} x {ny} x {nz} voxels [{vol.modality}]", icon="VOLUME_DATA")
            col.label(text=f"Voxel: {vs[0]:.2f} x {vs[1]:.2f} x {vs[2]:.2f} mm")
            for w in vol.warnings:
                col.label(text=w[:60] + ("..." if len(w) > 60 else ""), icon="ERROR")


class RIV_PT_load(_RIVPanel, bpy.types.Panel):
    bl_label = "1. Load Data"
    bl_idname = "RIV_PT_load"
    bl_parent_id = "RIV_PT_main"
    bl_order = 1

    def draw(self, context):
        st = context.scene.riv
        box = self.layout
        box.prop(st, "directory", text="")
        row = box.row(align=True)
        row.prop(st, "recursive")
        row.operator("riv.scan", icon="FILE_REFRESH")
        if _STATE["scan_info"]:
            box.label(text=_STATE["scan_info"])
        dec = _STATE.get("decoders")
        comp = ""
        if dec and 0 <= st.series_index < len(st.series):
            try:
                comp = _STATE["series"][st.series[st.series_index].index].compression
            except (IndexError, AttributeError):
                comp = ""
        if comp:
            ok = dec.get(comp, False)
            box.label(text=f"Compressed ({comp}): " + ("decoder available" if ok else "no decoder installed!"),
                      icon="CHECKMARK" if ok else "ERROR")
        if len(st.series):
            box.template_list("RIV_UL_series", "", st, "series", st, "series_index", rows=3)
            row = box.row(align=True)
            row.prop(st, "downsample")
            row.prop(st, "unit_scale")
            box.prop(st, "set_standard_view")
            box.operator("riv.load", icon="IMPORT")
        elif len(st.images2d):
            box.label(text="Only 2D images found: see '5. 2D Images'", icon="IMAGE_RGB")
        box.separator()
        box.operator("riv.clear", icon="TRASH")


class _NeedsVolume:
    @classmethod
    def poll(cls, context):
        return _STATE["volume"] is not None


class RIV_PT_cameras(_RIVPanel, _NeedsVolume, bpy.types.Panel):
    bl_label = "Cameras"
    bl_idname = "RIV_PT_cameras"
    bl_parent_id = "RIV_PT_main"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="CAMERA_DATA")

    def draw(self, context):
        st = context.scene.riv
        box = self.layout
        if all(bpy.data.objects.get(c[0]) for c in CAMERAS):
            row = box.row(align=True)
            row.prop(st, "cam_index")
            row.prop(st, "cam_ortho", text="Ortho", toggle=True)
            box.row(align=True).prop(st, "cam_align", expand=True)
            box.label(text=CAMERAS[st.cam_index - 1][3])
        else:
            box.operator("riv.make_cameras", icon="CAMERA_DATA")


class RIV_PT_slices(_RIVPanel, _NeedsVolume, bpy.types.Panel):
    bl_label = "2. Slice Planes"
    bl_idname = "RIV_PT_slices"
    bl_parent_id = "RIV_PT_main"
    bl_order = 3

    def draw(self, context):
        st = context.scene.riv
        box = self.layout
        col = box.column(align=True)
        col.prop(st, "slice_axial")
        col.prop(st, "slice_coronal")
        col.prop(st, "slice_sagittal")
        row = box.row(align=True)
        row.prop(st, "view_preset", text="")
        row.operator("riv.auto_window", icon="AUTO")
        row = box.row(align=True)
        row.prop(st, "window_center")
        row.prop(st, "window_width")
        col = box.column(align=True)
        col.prop(st, "contrast", slider=True)
        col.prop(st, "brightness", slider=True)
        row = box.row(align=True)
        row.prop(st, "invert", toggle=True)
        row.operator("riv.reset_tone", icon="LOOP_BACK")


class RIV_PT_mesh(_RIVPanel, _NeedsVolume, bpy.types.Panel):
    bl_label = "3. Surface Mesh"
    bl_idname = "RIV_PT_mesh"
    bl_parent_id = "RIV_PT_main"
    bl_order = 4

    def draw(self, context):
        st = context.scene.riv
        vol = _STATE["volume"]
        box = self.layout
        box.prop(st, "mesh_preset")
        if vol.modality != "CT" and st.mesh_preset != "CUSTOM":
            col = box.column(align=True)
            col.alert = True
            col.label(text=f"HU presets are for CT, this is {vol.modality}:", icon="ERROR")
            col.label(text="use Custom + Suggest")
        row = box.row(align=True)
        row.prop(st, "threshold")
        row.operator("riv.suggest_threshold", icon="AUTO")
        rng = _STATE.get("range")
        if rng is not None and not (rng[0] < st.threshold < rng[1]):
            col = box.column(align=True)
            col.alert = True
            col.label(text=f"Outside the data ({rng[0]:.0f} to {rng[1]:.0f})", icon="ERROR")
        row = box.row(align=True)
        row.prop(st, "mesh_step")
        row.prop(st, "close_ends")
        col = box.column(align=True)
        col.label(text="Smoothing:")
        col.prop(st, "smooth_mm", text="Noise Filter (mm)")
        col.prop(st, "edge_preserve", text="Edge-Preserving (keeps thin vessels, slower)")
        row = col.row(align=True)
        row.prop(st, "relax_iter")
        row.prop(st, "island_pct", text="Islands")
        box.operator("riv.make_mesh", icon="MESH_DATA")
        heavy = _heavy_subsurf_objects()
        if heavy:
            col = box.column(align=True)
            col.alert = True
            col.label(text="Subdivision on a huge mesh can crash Blender!", icon="ERROR")
            col.label(text="Use Noise Filter/Relax instead: " + ", ".join(heavy)[:40])


class RIV_PT_display(_RIVPanel, _NeedsVolume, bpy.types.Panel):
    bl_label = "4. Display"
    bl_idname = "RIV_PT_display"
    bl_parent_id = "RIV_PT_main"
    bl_order = 5

    def draw(self, context):
        st = context.scene.riv
        box = self.layout
        box.row(align=True).prop(st, "display_mode", expand=True)
        if st.display_mode == "GLASS":
            col = box.column(align=True)
            col.prop(st, "glass_color")
            col.prop(st, "glass_brightness")
            col.prop(st, "glass_edge")
            col.prop(st, "glass_core", slider=True)
            if not rm.riv_meshes():
                box.label(text="Create a mesh first", icon="INFO")
        elif st.display_mode == "VOLUME":
            box.row(align=True).prop(st, "vr_shading", expand=True)
            col = box.column(align=True)
            col.prop(st, "vr_preset")
            row = col.row(align=True)
            row.prop(st, "vr_low")
            row.prop(st, "vr_high")
            col.prop(st, "vr_opacity")
            col.prop(st, "vr_brightness")
            if st.vr_shading == "LIT":
                col = box.column(align=True)
                col.prop(st, "vr_anisotropy", slider=True)
                col.prop(st, "vr_glow")
                if context.scene.render.engine != "CYCLES":
                    box.label(text="Lit looks best in Cycles (EEVEE: soft preview)", icon="INFO")
            ramp_c, _ramp_o = rm.ramp_nodes()
            if ramp_c is None and bpy.data.objects.get(rm.VOLUME_OBJ) is not None:
                box.label(text="Press Rebuild for Glow/Lit and the curve editor", icon="INFO")
            row = box.row(align=True)
            row.prop(st, "vr_reduction", text="Reduction")
            has_vol = bpy.data.objects.get(rm.VOLUME_OBJ) is not None
            row.operator("riv.build_volume", text="Rebuild" if has_vol else "Build Volume",
                         icon="FILE_REFRESH" if has_vol else "VOLUME_DATA")
            if _STATE["vol_info"]:
                box.label(text=_STATE["vol_info"][:60])
        sh = getattr(context.space_data, "shading", None)
        if st.display_mode != "SURFACE" and sh is not None and sh.type in ("SOLID", "WIREFRAME"):
            box.label(text="Switch the viewport to Material Preview", icon="SHADING_TEXTURE")


class RIV_PT_transfer(_RIVPanel, bpy.types.Panel):
    bl_label = "Transfer Function"
    bl_idname = "RIV_PT_transfer"
    bl_parent_id = "RIV_PT_display"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return (_STATE["volume"] is not None and context.scene.riv.display_mode == "VOLUME"
                and rm.ramp_nodes()[0] is not None)

    def draw(self, context):
        ramp_c, ramp_o = rm.ramp_nodes()
        lay = self.layout
        lay.label(text="Colors (Low -> High):")
        lay.template_color_ramp(ramp_c, "color_ramp", expand=True)
        lay.label(text="Opacity (black = invisible):")
        lay.template_color_ramp(ramp_o, "color_ramp", expand=True)


class RIV_PT_2d(_RIVPanel, bpy.types.Panel):
    bl_label = "5. 2D Images (X-Ray)"
    bl_idname = "RIV_PT_2d"
    bl_parent_id = "RIV_PT_main"
    bl_order = 6

    @classmethod
    def poll(cls, context):
        return len(context.scene.riv.images2d) > 0 or bool(image2d.planes())

    def draw(self, context):
        st = context.scene.riv
        lay = self.layout
        if len(st.images2d):
            lay.template_list("RIV_UL_images2d", "", st, "images2d", st, "images2d_index", rows=3)
            row = lay.row(align=True)
            row.operator("riv.import_2d", text="Import Selected", icon="IMPORT").import_all = False
            row.operator("riv.import_2d", text="Import All", icon="IMAGE_RGB").import_all = True
        obj = context.active_object
        if obj is not None and obj.get(image2d.PROP):
            box = lay.box()
            box.label(text=f"{obj.name}: {obj.get('riv_description', '')}"[:60], icon="IMAGE_RGB")
            row = box.row(align=True)
            row.prop(obj.riv_2d, "level")
            row.prop(obj.riv_2d, "window")
            row = box.row(align=True)
            row.prop(obj.riv_2d, "invert", toggle=True)
            row.operator("riv.reset_2d", icon="LOOP_BACK")
        elif image2d.planes():
            lay.label(text="Select an image plane to adjust it", icon="INFO")


class RIV_PT_about(_RIVPanel, bpy.types.Panel):
    bl_label = "About"
    bl_idname = "RIV_PT_about"
    bl_parent_id = "RIV_PT_main"
    bl_order = 99

    def draw(self, context):
        lay = self.layout
        col = lay.column(align=True)
        title = ADDON_INFO["name"] + (f" {ADDON_INFO['version']}" if ADDON_INFO["version"] else "")
        col.label(text=title, icon="INFO")
        if ADDON_INFO["maintainer"]:
            col.label(text=f"by {ADDON_INFO['maintainer']}")
        if ADDON_INFO["website"]:
            url = ADDON_INFO["website"]
            op = lay.operator("wm.url_open", text=url.split("://", 1)[-1], icon="URL")
            op.url = url
        row = lay.row(align=True)
        op = row.operator("wm.url_open", text="Source Code", icon="FILE_SCRIPT")
        op.url = REPO_URL
        op = row.operator("wm.url_open", text="Report a Bug", icon="ERROR")
        op.url = ISSUES_URL
        lay.label(text="License: GPL-3.0-or-later")


PANELS = (RIV_PT_main, RIV_PT_load, RIV_PT_cameras, RIV_PT_slices, RIV_PT_mesh, RIV_PT_display, RIV_PT_transfer,
          RIV_PT_2d, RIV_PT_about)


# ----------------------------------------------------------------------------------------
# Handler: Laufzeit-Zustand passend zur geöffneten Datei halten
# ----------------------------------------------------------------------------------------

@persistent
def _on_load_post(*_args):
    # Ein im Speicher liegendes Volumen gehört zur vorherigen Datei. Würde es bleiben,
    # zeichneten die Slider fremde Bilddaten auf gleichnamige Ebenen der neuen Datei.
    _STATE["volume"] = None
    _STATE["series"] = []
    _STATE["images2d"] = []
    _STATE["scan_info"] = ""
    _STATE["vol_info"] = ""
    _STATE["range"] = None
    _STATE["busy"] = False


@persistent
def _on_undo_redo(*_args):
    # Generierte Bilder überleben Undo nicht mit Inhalt -> neu zeichnen
    if _STATE["volume"] is not None:
        try:
            refresh_planes()
        except Exception:
            pass


_HANDLERS = (
    (bpy.app.handlers.load_post, _on_load_post),
    (bpy.app.handlers.undo_post, _on_undo_redo),
    (bpy.app.handlers.redo_post, _on_undo_redo),
)

classes = (RIV_Series, RIV_Image2D, RIV_2DPlane, RIV_Settings, RIV_OT_scan, RIV_OT_load, RIV_OT_auto_window,
           RIV_OT_reset_tone, RIV_OT_suggest_threshold, RIV_OT_cameras, RIV_OT_mesh, RIV_OT_build_volume,
           RIV_OT_import_2d, RIV_OT_reset_2d, RIV_OT_clear, RIV_UL_series, RIV_UL_images2d) + PANELS


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.riv = PointerProperty(type=RIV_Settings)
    bpy.types.Object.riv_2d = PointerProperty(type=RIV_2DPlane)
    for lst, fn in _HANDLERS:
        if fn not in lst:
            lst.append(fn)


def unregister():
    for lst, fn in _HANDLERS:
        while fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_deferred_build_volume):
        bpy.app.timers.unregister(_deferred_build_volume)
    _STATE["volume"] = None
    del bpy.types.Scene.riv
    del bpy.types.Object.riv_2d
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

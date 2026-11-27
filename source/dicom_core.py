# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""DICOM einlesen: Serien finden, Schichten sortieren, HU-Volumen + Affine aufbauen.

Keine bpy-Abhängigkeit (separat testbar). Koordinaten: DICOM-Patientenraum (LPS, mm).
Voxelindex (i, j, k) = (Spalte, Zeile, Schicht); Volumen-Array: data[k, j, i].
"""
import os
from dataclasses import dataclass, field

import numpy as np

SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".txt", ".xml", ".html", ".htm",
    ".exe", ".dll", ".ini", ".inf", ".pdf", ".db", ".json", ".csv", ".zip", ".py", ".md",
    ".js", ".css", ".dat", ".log", ".lnk", ".bat", ".sh", ".app", ".plist",
}

# Nur diese Tags werden beim Scannen gelesen (schnell, und keine Patientendaten im Speicher)
_HEADER_TAGS = [
    "SeriesInstanceUID", "SeriesDescription", "SeriesNumber", "Modality", "Rows", "Columns",
    "ImagePositionPatient", "ImageOrientationPatient", "PixelSpacing", "ImagerPixelSpacing",
    "SliceThickness", "InstanceNumber", "NumberOfFrames",
    # für 2D-Einzelbilder (Röntgen usw.)
    "ViewPosition", "BodyPartExamined", "ImageLaterality", "SamplesPerPixel",
]


def get_pydicom():
    """pydicom importieren. Blender installiert das mitgelieferte Wheel beim Installieren
    der Extension selbst; das Add-on ändert weder sys.path noch schreibt es in seinen Ordner."""
    try:
        import pydicom
    except ImportError as e:
        raise ImportError(
            "pydicom is not available. Install Radiological Image Visualizer as an extension "
            "(Preferences > Get Extensions > Install from Disk) so Blender installs the bundled wheels."
        ) from e
    return pydicom


_DECODER_GROUPS = (
    ("JPEG", ("JPEGBaseline8Bit", "JPEGExtended12Bit", "JPEGLosslessSV1", "JPEGLossless")),
    ("JPEG-LS", ("JPEGLSLossless", "JPEGLSNearLossless")),
    ("JPEG2000", ("JPEG2000Lossless", "JPEG2000")),
    ("RLE", ("RLELossless",)),
)


def compression_name(ds):
    """Kurzname der Kompression einer Datei ("" = unkomprimiert)."""
    ts = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
    if ts is None or not getattr(ts, "is_compressed", False):
        return ""
    kw = getattr(ts, "keyword", "") or ""
    for name, uids in _DECODER_GROUPS:
        if kw in uids:
            return name
    return getattr(ts, "name", "compressed")


def decoder_status():
    """{'JPEG': True, 'JPEG-LS': True, 'JPEG2000': False, 'RLE': True} - welche komprimierten
    DICOM-Formate dekodiert werden können. Die Decoder (pylibjpeg + Plugins) liegen als
    Wheels im Add-on; Blender installiert die passenden für System und Python-Version."""
    status = {}
    try:
        get_pydicom()
        import pydicom.uid as U
        from pydicom.pixels import get_decoder
    except Exception:
        return {name: False for name, _ in _DECODER_GROUPS}
    for name, uids in _DECODER_GROUPS:
        ok = False
        for u in uids:
            uid = getattr(U, u, None)
            if uid is None:
                continue
            try:
                if get_decoder(uid).is_available:
                    ok = True
                    break
            except Exception:
                pass
        status[name] = ok
    return status


@dataclass
class SeriesInfo:
    uid: str
    description: str
    modality: str
    series_number: int
    rows: int
    cols: int
    pixel_spacing: tuple          # (Zeilenabstand, Spaltenabstand) in mm
    orientation: tuple            # 6 Richtungskosinus
    thickness: float              # SliceThickness aus dem Header (mm), 0 wenn unbekannt
    compression: str = ""         # "", "JPEG", "JPEG-LS", "JPEG2000", "RLE" oder Name der Transfer Syntax
    entries: list = field(default_factory=list)   # [(pfad, ipp ndarray(3), instance)]
    # nach finalize():
    files: list = field(default_factory=list)
    positions: np.ndarray = None
    slice_spacing: float = 0.0
    slice_vec: np.ndarray = None
    origin: np.ndarray = None
    warnings: list = field(default_factory=list)

    @property
    def count(self):
        return len(self.files) if self.files else len(self.entries)

    def label(self):
        desc = self.description or "(no description)"
        sp = f"{self.slice_spacing:.2f} mm" if self.slice_spacing else "?"
        comp = f", {self.compression}" if self.compression else ""
        return f"#{self.series_number} {desc} [{self.modality}] {self.count} slices, {self.cols}x{self.rows}, dz {sp}{comp}"


@dataclass
class Image2DInfo:
    """Ein einzelnes 2D-Bild ohne 3D-Serie (Röntgen, Mammographie, Einzelschicht ...)."""
    path: str
    modality: str
    description: str
    rows: int
    cols: int
    pixel_spacing: tuple          # (Zeilenabstand, Spaltenabstand) mm; (0, 0) = unbekannt
    series_number: int = 0
    instance: int = 0
    compression: str = ""

    def label(self):
        sp = "" if not self.pixel_spacing[0] else (
            f", {self.cols * self.pixel_spacing[1] / 10:.1f} x {self.rows * self.pixel_spacing[0] / 10:.1f} cm")
        comp = f", {self.compression}" if self.compression else ""
        return f"#{self.series_number} {self.description or '(no description)'} [{self.modality}] {self.cols}x{self.rows}{sp}{comp}"


@dataclass
class ScanResult:
    series: list
    images2d: list = field(default_factory=list)
    n_files: int = 0
    n_skipped: int = 0
    n_multiframe: int = 0


@dataclass
class Volume:
    data: np.ndarray              # float32, [k, j, i], Hounsfield bzw. Signalwerte
    affine: np.ndarray            # 4x4, Voxelindex -> Patientenraum (LPS, mm)
    modality: str
    description: str
    window: tuple                 # (center, width)
    warnings: list
    downsample: int = 1

    @property
    def voxel_size(self):
        return np.linalg.norm(self.affine[:3, :3], axis=0)


def _iter_files(root, recursive):
    if recursive:
        for d, _, names in os.walk(root):
            for n in sorted(names):
                yield os.path.join(d, n)
    else:
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if os.path.isfile(p):
                yield p


def scan_directory(root, recursive=True, progress=None):
    """Alle DICOM-Bilddateien unter root lesen (nur Header) und zu Serien gruppieren."""
    pydicom = get_pydicom()
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Folder not found: {root}")

    groups = {}
    res = ScanResult(series=[])
    for path in _iter_files(root, recursive):
        name = os.path.basename(path)
        if name.startswith(".") or name.upper() == "DICOMDIR" or os.path.splitext(name)[1].lower() in SKIP_EXT:
            continue
        res.n_files += 1
        if progress and res.n_files % 50 == 0:
            progress(res.n_files)
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_HEADER_TAGS)
        except Exception:
            res.n_skipped += 1
            continue
        if not all(k in ds for k in ("Rows", "Columns", "SeriesInstanceUID")):
            res.n_skipped += 1                # kein Bild (Bericht, Struktur, ...)
            continue
        if int(ds.get("NumberOfFrames", 1) or 1) > 1:
            res.n_multiframe += 1
            continue
        if not all(k in ds for k in ("ImagePositionPatient", "ImageOrientationPatient")):
            # Bild ohne Lage im Raum (Röntgen, Foto, ...) -> nur als 2D-Bild nutzbar
            img = _image2d_from_header(ds, path)
            if img is not None:
                res.images2d.append(img)
            else:
                res.n_skipped += 1
            continue
        try:
            iop = tuple(round(float(x), 3) for x in ds.ImageOrientationPatient)
            ipp = np.array([float(x) for x in ds.ImagePositionPatient], dtype=float)
            ps = ds.get("PixelSpacing", None) or ds.get("ImagerPixelSpacing", None) or [1.0, 1.0]
            ps = (float(ps[0]), float(ps[1]))
            rows, cols = int(ds.Rows), int(ds.Columns)
        except Exception:
            res.n_skipped += 1
            continue
        key = (str(ds.SeriesInstanceUID), rows, cols, iop)
        s = groups.get(key)
        if s is None:
            try:
                snum = int(ds.get("SeriesNumber", 0) or 0)
            except Exception:
                snum = 0
            s = SeriesInfo(
                uid=str(ds.SeriesInstanceUID),
                description=str(ds.get("SeriesDescription", "") or ""),
                modality=str(ds.get("Modality", "") or "?"),
                series_number=snum, rows=rows, cols=cols, pixel_spacing=ps, orientation=iop,
                thickness=float(ds.get("SliceThickness", 0) or 0),
                compression=compression_name(ds),
            )
            groups[key] = s
        s.entries.append((path, ipp, int(ds.get("InstanceNumber", 0) or 0)))

    for s in groups.values():
        _finalize(s)
        if s.count >= 2:
            res.series.append(s)
        elif s.files:
            # Serie mit nur einer Schicht: als 2D-Bild anbieten
            try:
                ds = pydicom.dcmread(s.files[0], stop_before_pixels=True, specific_tags=_HEADER_TAGS)
                img = _image2d_from_header(ds, s.files[0])
                if img is not None:
                    res.images2d.append(img)
            except Exception:
                res.n_skipped += 1
    res.series.sort(key=lambda s: (s.series_number, -s.count))
    res.images2d.sort(key=lambda i: (i.series_number, i.instance, i.path))
    return res


def _image2d_from_header(ds, path):
    try:
        rows, cols = int(ds.Rows), int(ds.Columns)
    except Exception:
        return None
    if rows < 2 or cols < 2:
        return None
    ps = ds.get("PixelSpacing", None) or ds.get("ImagerPixelSpacing", None)
    try:
        ps = (float(ps[0]), float(ps[1])) if ps else (0.0, 0.0)
    except Exception:
        ps = (0.0, 0.0)
    desc = str(ds.get("SeriesDescription", "") or "")
    view = str(ds.get("ViewPosition", "") or "")
    part = str(ds.get("BodyPartExamined", "") or "")
    if not desc:
        desc = " ".join(x for x in (part, view) if x)
    elif view and view.lower() not in desc.lower():
        desc = f"{desc} ({view})"
    try:
        snum = int(ds.get("SeriesNumber", 0) or 0)
        inst = int(ds.get("InstanceNumber", 0) or 0)
    except Exception:
        snum, inst = 0, 0
    return Image2DInfo(path=path, modality=str(ds.get("Modality", "") or "?"), description=desc,
                       rows=rows, cols=cols, pixel_spacing=ps, series_number=snum, instance=inst,
                       compression=compression_name(ds))


def _finalize(s):
    """Schichten entlang der Schichtnormalen sortieren, Duplikate entfernen, Abstand bestimmen."""
    row = np.array(s.orientation[:3], float)
    col = np.array(s.orientation[3:], float)
    normal = np.cross(row, col)
    normal /= (np.linalg.norm(normal) or 1.0)

    pos = np.array([float(e[1] @ normal) for e in s.entries])
    order = np.argsort(pos, kind="stable")
    entries = [s.entries[i] for i in order]
    pos = pos[order]

    keep = np.ones(len(pos), bool)
    keep[1:] = np.diff(pos) > 1e-3
    n_dup = int((~keep).sum())
    entries = [e for e, k in zip(entries, keep) if k]
    pos = pos[keep]
    if n_dup:
        s.warnings.append(f"{n_dup} slice(s) at an identical position ignored (e.g. several phases/echoes in one series).")

    s.files = [e[0] for e in entries]
    s.positions = pos
    s.entries = []  # Speicher freigeben; Pfade stehen in s.files
    n = len(s.files)
    if n < 2:
        s.slice_spacing = s.thickness
        s.slice_vec = normal * (s.thickness or 1.0)
        s.origin = entries[0][1] if entries else np.zeros(3)
        return

    ipp_first, ipp_last = entries[0][1], entries[-1][1]
    s.origin = ipp_first
    s.slice_vec = (ipp_last - ipp_first) / (n - 1)
    s.slice_spacing = float(np.linalg.norm(s.slice_vec))

    diffs = np.diff(pos)
    med = float(np.median(diffs))
    if med > 0 and np.abs(diffs - med).max() > 0.1 * med + 1e-3:
        s.warnings.append("Uneven slice spacing (gaps?) - geometry is only approximate.")
    cosang = abs(float(s.slice_vec @ normal)) / (s.slice_spacing or 1.0)
    if cosang < 0.999:
        s.warnings.append("Slices stacked at an angle (gantry tilt): the voxel grid is shown sheared.")
    if s.thickness and abs(s.thickness - s.slice_spacing) > 0.25 * s.slice_spacing:
        s.warnings.append(
            f"SliceThickness ({s.thickness:.2f} mm) differs from the slice spacing ({s.slice_spacing:.2f} mm) "
            "(overlap or gap). The slice spacing is used for the geometry.")


def build_affine(s):
    """4x4: Voxelindex (i=Spalte, j=Zeile, k=Schicht) -> Patientenraum LPS in mm."""
    row = np.array(s.orientation[:3], float)
    col = np.array(s.orientation[3:], float)
    A = np.eye(4)
    A[:3, 0] = row * s.pixel_spacing[1]   # Spaltenindex läuft entlang der Zeilenrichtung
    A[:3, 1] = col * s.pixel_spacing[0]   # Zeilenindex läuft entlang der Spaltenrichtung
    A[:3, 2] = s.slice_vec
    A[:3, 3] = s.origin
    return A


def suggest_window(data, modality):
    if modality == "CT":
        return 40.0, 400.0
    step = max(1, data.shape[0] // 16)
    sample = data[::step, ::4, ::4]
    lo, hi = np.percentile(sample, [1.0, 99.5])
    if hi <= lo:
        hi = lo + 1.0
    return float((lo + hi) / 2), float(hi - lo)


def _pixels(ds, path):
    """Pixel als float32 mit Rescale; MONOCHROME1 wird invertiert (hell = dicht).
    Gibt (arr, flip) zurück: flip = Summe, um die ein Wert x zu flip - x gespiegelt wurde (oder None)."""
    try:
        arr = ds.pixel_array
    except Exception as e:
        ts = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
        ts_name = getattr(ts, "name", str(ts)) if ts is not None else "unknown"
        if ts is not None and getattr(ts, "is_compressed", False):
            hint = ("No decoder for this compression is available in this Blender installation "
                    "(bundled for Windows x64, macOS Apple Silicon, macOS Intel up to Blender 5.0, Linux x64). "
                    "Try reinstalling the add-on, or export the series uncompressed.")
        else:
            hint = "The file may be damaged or use an unusual pixel format."
        raise RuntimeError(
            f"Cannot read image data in '{os.path.basename(path)}' ({ts_name}): {e}. {hint}"
        ) from e
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 3:                      # RGB -> Helligkeit
        arr = arr.mean(axis=-1)
    slope = float(ds.get("RescaleSlope", 1) or 1)
    intercept = float(ds.get("RescaleIntercept", 0) or 0)
    arr = arr * slope + intercept
    flip = None
    if str(ds.get("PhotometricInterpretation", "")) == "MONOCHROME1":
        flip = float(arr.max() + arr.min())
        arr = flip - arr
    return arr, flip


def _first(value):
    try:
        if isinstance(value, (list, tuple)) or hasattr(value, "__getitem__") and not isinstance(value, str):
            return float(value[0])
        return float(value)
    except Exception:
        return None


def load_image2d(info):
    """2D-Bild lesen -> (arr float32 [rows, cols], (center, width)).
    Fensterung aus dem Header, sonst aus dem 1.-99.5. Perzentil."""
    pydicom = get_pydicom()
    ds = pydicom.dcmread(info.path)
    arr, flip = _pixels(ds, info.path)
    c = _first(ds.get("WindowCenter", None))
    w = _first(ds.get("WindowWidth", None))
    if c is not None and w is not None and w > 1:
        # VOI-Werte beziehen sich auf die Werte nach Rescale (DICOM PS3.3 C.11.2);
        # bei MONOCHROME1 wurde invertiert -> Fenstermitte mitspiegeln
        if flip is not None:
            c = flip - c
        window = (c, w)
    else:
        lo, hi = np.percentile(arr[::4, ::4], [1.0, 99.5])
        window = (float((lo + hi) / 2), float(max(hi - lo, 1.0)))
    return arr, window


def load_series(s, downsample=1, progress=None):
    """Serie vollständig lesen -> Volume (float32, HU). downsample = Mittelwert-Pooling-Faktor."""
    pydicom = get_pydicom()
    f = max(1, int(downsample))
    n = len(s.files)
    if n < 2:
        raise ValueError("A volume needs at least 2 slices.")

    data = None
    for k, path in enumerate(s.files):
        if progress:
            progress(k)
        ds = pydicom.dcmread(path)
        arr, _flip = _pixels(ds, path)
        if f > 1:
            h, w = arr.shape[0] // f * f, arr.shape[1] // f * f
            arr = arr[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
        if data is None:
            data = np.empty((n,) + arr.shape, np.float32)
        elif arr.shape != data.shape[1:]:
            raise RuntimeError(f"Inconsistent image size within the series ({os.path.basename(path)}).")
        data[k] = arr

    if f > 1:
        nz = n // f * f
        data = data[:nz].reshape(nz // f, f, data.shape[1], data.shape[2]).mean(axis=1, dtype=np.float32)

    A = build_affine(s)
    if f > 1:
        # Pooled-Voxel (i,j,k) liegt im Originalraster bei (i+0.5)*f-0.5
        P = np.diag([f, f, f, 1.0])
        P[:3, 3] = (f - 1) / 2.0
        A = A @ P

    return Volume(
        data=data, affine=A, modality=s.modality, description=s.description,
        window=suggest_window(data, s.modality), warnings=list(s.warnings), downsample=f,
    )


def apply_window(slice2d, center, width):
    """HU -> 0..1 (Fensterung)."""
    width = max(float(width), 1e-6)
    return np.clip((slice2d - (center - width / 2.0)) / width, 0.0, 1.0).astype(np.float32)

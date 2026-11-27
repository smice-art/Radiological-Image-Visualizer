# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Isofläche aus einem Skalarvolumen (reines NumPy, keine bpy-Abhängigkeit; SciPy optional).

Pipeline (extract_surface): Pooling -> Rauschfilter (Gauß oder kantenerhaltend) ->
Surface Nets -> lose Teile entfernen -> eingeschränkte Taubin-Glättung.

Verfahren: Naive Surface Nets. Pro Zelle, die den Schwellwert schneidet, entsteht
ein Vertex im Mittel der Kantenschnittpunkte; pro Gitterkante mit Vorzeichenwechsel
entsteht ein Quad aus den vier angrenzenden Zellen. Vorteile gegenüber Marching Cubes:
keine großen Lookup-Tabellen, glattere Ergebnisse, Quads statt Dreiecken.

Konvention: vol[z, y, x]; Ausgabe-Vertices in Indexkoordinaten (x, y, z).
"inside" bedeutet vol >= threshold. Die Quads sind so gewickelt, dass die Normalen
von "inside" nach "outside" zeigen.
"""
import numpy as np

# Die 12 Würfelkanten als Paare von Eckversätzen (dz, dy, dx)
_CORNERS = [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
            (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)]
_EDGES = []
for _c in _CORNERS:
    for _axis in range(3):
        if _c[_axis] == 0:
            _d = list(_c)
            _d[_axis] = 1
            _EDGES.append((_c, tuple(_d)))
assert len(_EDGES) == 12


def pool(vol, s):
    """Mittelwert-Pooling mit Faktor s in allen drei Achsen (Rest am Ende wird verworfen).

    Rückgabe-Voxel (i, j, k) entspricht dem Block-Mittelpunkt (i+0.5)*s-0.5 im
    Originalraster.
    """
    if s <= 1:
        return vol
    nz, ny, nx = (d // s * s for d in vol.shape)
    v = vol[:nz, :ny, :nx].reshape(nz // s, s, ny // s, s, nx // s, s)
    return v.mean(axis=(1, 3, 5), dtype=np.float32)


def surface_nets(vol, threshold, close_ends=True, return_cells=False):
    """Gibt (verts[N,3] float32 in Indexkoordinaten x,y,z; quads[M,4] int32) zurück.

    Mit return_cells=True zusätzlich cells[N,3] float32: untere Ecke (x,y,z) der Zelle,
    in der jeder Vertex liegt (für die eingeschränkte Glättung in relax()).
    """
    vol = np.asarray(vol, dtype=np.float32)
    threshold = float(threshold)
    offset = 0.0
    if close_ends:
        # Rand mit "außen"-Wert auffüllen, damit die Oberfläche an den Volumenenden geschlossen ist
        pad = min(float(vol.min()), threshold - 1.0)
        vol = np.pad(vol, 1, mode="constant", constant_values=pad)
        offset = -1.0
    vol = np.ascontiguousarray(vol)
    nz, ny, nx = vol.shape
    empty = (np.zeros((0, 3), np.float32), np.zeros((0, 4), np.int32))
    if return_cells:
        empty = empty + (np.zeros((0, 3), np.float32),)
    if min(nz, ny, nx) < 2:
        return empty

    inside = vol >= threshold

    # --- aktive Zellen: Ecken gemischt innen/außen ---
    cz, cy, cx = nz - 1, ny - 1, nx - 1
    any_in = np.zeros((cz, cy, cx), bool)
    all_in = np.ones((cz, cy, cx), bool)
    for dz, dy, dx in _CORNERS:
        v = inside[dz:dz + cz, dy:dy + cy, dx:dx + cx]
        any_in |= v
        all_in &= v
    active = any_in & ~all_in
    del any_in, all_in
    iz, iy, ix = np.nonzero(active)
    del active
    n = iz.size
    if n == 0:
        return empty

    # Lineare Zell-ID (aufsteigend sortiert, da nonzero zeilenweise liefert)
    lin = (iz.astype(np.int64) * cy + iy) * cx + ix

    # --- Vertex pro Zelle: Mittel der Kantenschnittpunkte ---
    flat = vol.ravel()
    base = (iz.astype(np.int64) * ny + iy) * nx + ix
    acc = np.zeros((n, 3), np.float32)  # (x, y, z) lokal
    cnt = np.zeros(n, np.float32)
    for a, b in _EDGES:
        off_a = (a[0] * ny + a[1]) * nx + a[2]
        off_b = (b[0] * ny + b[1]) * nx + b[2]
        va = flat[base + off_a]
        vb = flat[base + off_b]
        cross = (va >= threshold) != (vb >= threshold)
        if not cross.any():
            continue
        denom = np.where(cross, vb - va, np.float32(1.0))
        t = np.clip((threshold - va) / denom, 0.0, 1.0)
        for out_axis, idx_axis in ((0, 2), (1, 1), (2, 0)):  # x<-dx, y<-dy, z<-dz
            pos = a[idx_axis] + t * (b[idx_axis] - a[idx_axis])
            acc[:, out_axis] += np.where(cross, pos, 0.0)
        cnt += cross
    verts = acc / cnt[:, None]
    verts[:, 0] += ix
    verts[:, 1] += iy
    verts[:, 2] += iz
    verts += offset
    del acc, cnt, base

    # --- Quads pro Gitterkante mit Vorzeichenwechsel ---
    def cell_ids(cz_, cy_, cx_):
        l = (cz_.astype(np.int64) * cy + cy_) * cx + cx_
        p = np.searchsorted(lin, l)
        p_c = np.minimum(p, n - 1)
        ok = lin[p_c] == l
        return p_c, ok

    quad_list = []

    def emit(cells, low_in):
        ids = [c[0] for c in cells]
        ok = cells[0][1] & cells[1][1] & cells[2][1] & cells[3][1]
        q = np.stack(ids, axis=1)
        # Reihenfolge A,B,C,D hat Normale in +Achse (von niedrigem zu hohem Voxel).
        # Liegt das niedrige Voxel innen, passt das; sonst umdrehen.
        q = np.where(low_in[:, None], q, q[:, [0, 3, 2, 1]])
        quad_list.append(q[ok].astype(np.int32))

    # x-Kanten: Voxel (z,y,x)-(z,y,x+1); Ebene (y,z), Normale +x
    e = inside[1:nz - 1, 1:ny - 1, :nx - 1] != inside[1:nz - 1, 1:ny - 1, 1:]
    ez, ey, ex = np.nonzero(e)
    ez = ez + 1
    ey = ey + 1
    if ez.size:
        low_in = inside[ez, ey, ex]
        emit([cell_ids(ez - 1, ey - 1, ex), cell_ids(ez - 1, ey, ex),
              cell_ids(ez, ey, ex), cell_ids(ez, ey - 1, ex)], low_in)

    # y-Kanten: Voxel (z,y,x)-(z,y+1,x); Ebene (z,x), Normale +y
    e = inside[1:nz - 1, :ny - 1, 1:nx - 1] != inside[1:nz - 1, 1:, 1:nx - 1]
    ez, ey, ex = np.nonzero(e)
    ez = ez + 1
    ex = ex + 1
    if ez.size:
        low_in = inside[ez, ey, ex]
        emit([cell_ids(ez - 1, ey, ex - 1), cell_ids(ez, ey, ex - 1),
              cell_ids(ez, ey, ex), cell_ids(ez - 1, ey, ex)], low_in)

    # z-Kanten: Voxel (z,y,x)-(z+1,y,x); Ebene (x,y), Normale +z
    e = inside[:nz - 1, 1:ny - 1, 1:nx - 1] != inside[1:, 1:ny - 1, 1:nx - 1]
    ez, ey, ex = np.nonzero(e)
    ey = ey + 1
    ex = ex + 1
    if ez.size:
        low_in = inside[ez, ey, ex]
        emit([cell_ids(ez, ey - 1, ex - 1), cell_ids(ez, ey - 1, ex),
              cell_ids(ez, ey, ex), cell_ids(ez, ey, ex - 1)], low_in)

    quads = np.concatenate(quad_list, axis=0) if quad_list else empty[1]
    if quads.size == 0:
        return empty

    # Ungenutzte Vertices entfernen und Indizes neu abbilden
    used = np.zeros(n, bool)
    used[quads.ravel()] = True
    remap = np.cumsum(used, dtype=np.int64) - 1
    quads = remap[quads].astype(np.int32)
    if return_cells:
        cells = np.stack([ix[used], iy[used], iz[used]], axis=1).astype(np.float32) + offset
        return verts[used].astype(np.float32), quads, cells
    return verts[used].astype(np.float32), quads


# ----------------------------------------------------------------------------------------
# Glättung und Aufräumen (reines NumPy; SciPy wird nur genutzt, wenn vorhanden -> schneller)
# ----------------------------------------------------------------------------------------

def _gauss_kernel(sigma):
    r = max(1, int(np.ceil(2.5 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    w = np.exp(-0.5 * (x / sigma) ** 2)
    return (w / w.sum()).astype(np.float32)


def gaussian_smooth(vol, sigma):
    """Rauschfilter: 3D-Gauß mit sigma in Voxeln (separierbar). sigma <= 0 -> unverändert.

    sigma ist ein Tupel (sz, sy, sx) oder eine Zahl. Anisotrope Voxel (z. B. 0.4 x 0.4 x 0.6 mm)
    werden so in allen Richtungen gleich stark in Millimetern geglättet.
    """
    if np.isscalar(sigma):
        sigma = (sigma, sigma, sigma)
    sigma = tuple(float(s) for s in sigma)
    if max(sigma) <= 0.05:
        return vol
    vol = np.asarray(vol, dtype=np.float32)
    out = vol
    for axis, s in enumerate(sigma):
        if s <= 0.05:
            continue
        w = _gauss_kernel(s)
        r = len(w) // 2
        n = out.shape[axis]
        pad = [(0, 0)] * 3
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros(out.shape, np.float32)
        tmp = np.empty(out.shape, np.float32)
        for i, wi in enumerate(w):
            sl = [slice(None)] * 3
            sl[axis] = slice(i, i + n)
            np.multiply(p[tuple(sl)], wi, out=tmp)
            acc += tmp
        del p, tmp
        out = acc
    return out


def estimate_noise(vol):
    """Robuste Schätzung des Bildrauschens (Sigma) aus Nachbar-Differenzen im Objekt."""
    s = np.asarray(vol[::4, ::2, :], dtype=np.float32)
    d = np.abs(np.diff(s, axis=2))
    lo = np.percentile(s[::2, ::4, ::4], 20)
    m = (s[:, :, 1:] > lo) & (s[:, :, :-1] > lo)
    if m.sum() < 100:
        m = np.ones_like(m)
    return float(1.4826 * np.median(d[m]) / np.sqrt(2.0)) or 1.0


def edge_preserving_smooth(vol, iterations=10, K=None, voxel_size=(1.0, 1.0, 1.0), progress=None):
    """Anisotrope Diffusion (Perona-Malik): glättet Rauschen in gleichmäßigen Bereichen
    stark, an Gewebegrenzen (große Sprünge) kaum -> Gefäße und Kanten bleiben scharf.

    K: Kantenschwelle in Datenwerten; None = 4 x geschätztes Rauschen.
    voxel_size (sx, sy, sz): dickere Schichten werden schwächer gekoppelt.
    """
    u = np.array(vol, dtype=np.float32, copy=True)
    if iterations <= 0:
        return u
    if K is None:
        K = 4.0 * estimate_noise(u)
    K = max(float(K), 1e-6)
    vs = np.maximum(np.asarray(voxel_size, float), 1e-9)
    vmin = float(vs.min())
    w = [(vmin / vs[2]) ** 2, (vmin / vs[1]) ** 2, (vmin / vs[0]) ** 2]   # Array-Achsen z, y, x
    dt = 1.0 / 7.0                                                    # stabil für 3D (< 1/6)
    inv_k2 = np.float32(1.0 / (K * K))
    for it in range(int(iterations)):
        for axis in range(3):
            d = np.diff(u, axis=axis)
            g = d * d
            g *= -inv_k2
            np.exp(g, out=g)
            g *= d
            g *= np.float32(dt * w[axis])
            lo = [slice(None)] * 3
            hi = [slice(None)] * 3
            lo[axis] = slice(0, -1)
            hi[axis] = slice(1, None)
            u[tuple(lo)] += g
            u[tuple(hi)] -= g
            del d, g
        if progress:
            progress(it)
    return u


def suggest_threshold(vol):
    """Otsu-Schwelle: trennt die zwei Hauptgruppen der Werte (meist Hintergrund / Gewebe).
    Sinnvoller Startwert für MRT, wo es keine festen Werte wie HU gibt."""
    step = max(1, vol.shape[0] // 32)
    s = np.asarray(vol[::step, ::4, ::4], dtype=np.float64).ravel()
    lo, hi = np.percentile(s, [0.5, 99.5])
    if hi <= lo:
        return float(lo)
    hist, edges = np.histogram(s, bins=256, range=(lo, hi))
    p = hist.astype(np.float64) / max(hist.sum(), 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(p)
    m0 = np.cumsum(p * centers)
    mt = m0[-1]
    w1 = 1.0 - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w0 - m0) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = 0.0
    return float(edges[int(np.argmax(between)) + 1])


def _components(n, a, b):
    """Zusammenhangskomponenten eines Graphen mit n Knoten und Kanten (a[i], b[i]).

    Gibt pro Knoten eine Komponenten-ID zurück (Union-Find mit Pointer-Jumping, vektorisiert).
    """
    parent = np.arange(n, dtype=np.int64)
    a = a.astype(np.int64)
    b = b.astype(np.int64)
    for _ in range(200):
        pa = parent[a]
        pb = parent[b]
        m = pa != pb
        if not m.any():
            break
        a, b, pa, pb = a[m], b[m], pa[m], pb[m]
        lo = np.minimum(pa, pb)
        hi = np.maximum(pa, pb)
        np.minimum.at(parent, hi, lo)
        while True:
            pp = parent[parent]
            if np.array_equal(pp, parent):
                break
            parent = pp
    return parent


def remove_islands(verts, quads, min_fraction, cells=None):
    """Entfernt lose Teile (Rauschen, Tisch-Fragmente), die kleiner sind als
    min_fraction x Flächenzahl des größten Teils. min_fraction <= 0 -> unverändert."""
    if min_fraction <= 0 or len(quads) == 0:
        return verts, quads, cells
    n = len(verts)
    q = quads.astype(np.int64)
    a = np.concatenate([q[:, 0], q[:, 0], q[:, 0]])
    b = np.concatenate([q[:, 1], q[:, 2], q[:, 3]])
    lab = _components(n, a, b)
    face_lab = lab[q[:, 0]]
    counts = np.bincount(face_lab, minlength=int(lab.max()) + 1)
    keep_comp = counts >= max(1.0, min_fraction * counts.max())
    keep = keep_comp[face_lab]
    quads = quads[keep]
    used = np.zeros(n, bool)
    used[quads.ravel()] = True
    remap = np.cumsum(used, dtype=np.int64) - 1
    quads = remap[quads].astype(np.int32)
    verts = verts[used]
    if cells is not None:
        cells = cells[used]
    return verts, quads, cells


def relax(verts, quads, iterations, cells=None, lam=0.6, mu=-0.63, slack=0.0):
    """Taubin-Glättung (lambda/mu): glättet Treppen und Restrauschen, ohne dass das
    Objekt schrumpft. Mit cells bleibt jeder Vertex in seiner Ursprungszelle
    (+/- slack Voxel) -> die Form kann nie mehr als ca. 1 Voxel von den Daten abweichen
    ("Constrained Surface Nets"). Arbeitet in Index-Koordinaten."""
    if iterations <= 0 or len(quads) == 0:
        return verts
    n = len(verts)
    q = quads.astype(np.int64)
    # Jede Quad-Kante in beide Richtungen; innere Kanten kommen 2x vor (gleichmäßig -> egal)
    a = np.concatenate([q[:, 0], q[:, 1], q[:, 2], q[:, 3]])
    b = np.concatenate([q[:, 1], q[:, 2], q[:, 3], q[:, 0]])
    x = verts.astype(np.float32).copy()
    if cells is not None:
        lo = cells - slack
        hi = cells + 1.0 + slack

    deg = (np.bincount(a, minlength=n) + np.bincount(b, minlength=n)).astype(np.float32)
    deg[deg == 0] = 1.0
    inv = 1.0 / deg

    def avg(p):
        out = np.empty_like(p)
        for c in range(3):
            pc = p[:, c]
            out[:, c] = (np.bincount(a, weights=pc[b], minlength=n) +
                         np.bincount(b, weights=pc[a], minlength=n)) * inv
        return out

    for _ in range(int(iterations)):
        for f in (lam, mu):
            x += f * (avg(x) - x)
            if cells is not None:
                np.clip(x, lo, hi, out=x)
    return x.astype(np.float32)


def extract_surface(vol, threshold, close_ends=True, step=1, smooth_sigma=0.0,
                    voxel_size=(1.0, 1.0, 1.0), relax_iter=0, island_fraction=0.0,
                    edge_preserve=False, edge_iterations=10, progress=None):
    """Komplette Pipeline. Gibt (verts[N,3] in Original-Indexkoordinaten x,y,z, quads[M,4]).

    vol           : [z, y, x] float32
    step          : Mittelwert-Pooling (Mesh Reduction)
    smooth_sigma  : Rauschfilter, Gauß-Sigma in Millimetern (0 = aus); wird pro Achse
                    auf die tatsächliche Voxelgröße umgerechnet
    voxel_size    : (sx, sy, sz) des Originalrasters in mm
    relax_iter    : Taubin-Iterationen mit Zell-Beschränkung
    island_fraction : lose Teile < Anteil des größten Teils entfernen
    edge_preserve : vorher anisotrope Diffusion (kantenerhaltend, langsamer)
    """
    def tick(i):
        if progress:
            progress(i)

    data = pool(vol, step)
    vs = np.maximum(np.asarray(voxel_size, float) * step, 1e-6)
    tick(1)
    if edge_preserve:
        data = edge_preserving_smooth(data, edge_iterations, None, vs)
        smooth_sigma *= 0.6        # danach reicht ein leichter Gauß gegen Restkörnung
    if smooth_sigma > 0:
        # sigma in mm -> Voxel je Achse; Array-Reihenfolge (z, y, x)
        sig = (smooth_sigma / vs[2], smooth_sigma / vs[1], smooth_sigma / vs[0])
        data = gaussian_smooth(data, sig)
    tick(2)
    verts, quads, cells = surface_nets(data, threshold, close_ends, return_cells=True)
    del data
    tick(3)
    if len(quads) == 0:
        return verts, quads
    verts, quads, cells = remove_islands(verts, quads, island_fraction, cells)
    tick(4)
    verts = relax(verts, quads, relax_iter, cells, slack=0.25)
    tick(5)
    if step > 1:
        verts = (verts + 0.5) * step - 0.5      # zurück ins Originalraster
    return verts.astype(np.float32), quads

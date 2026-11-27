# Radiological Image Visualizer (RIV) - User Guide

*Blender add-on, version 1.0.1 - explore CT and MRI series in 3D*

By Claudio - https://superhivemarket.com/creators/smice - License: GPL-3.0-or-later

**Disclaimer:** ⚠️ This add-on is a visualization tool for education, art and personal interest. It is not a medical device and must not be used for diagnosis or treatment.

## 1. What it does

Radiological Image Visualizer (short: RIV) reads a folder of DICOM files (CT or MRI), builds a 3D volume from the slices, and shows it inside Blender in complementary ways that share the same 3D space:

- **Slice planes**: three movable planes (axial, coronal, sagittal) that show the original image data without any loss, with adjustable window/level.
- **Surface mesh**: a smooth 3D model of a tissue (for example bone, skin or contrast-filled vessels) created at a threshold you choose. You can orbit around it, fly through it, cut it, render it and animate it like any other Blender object.
- **Display modes**: switch between *Surface* (solid meshes), *Glass* (see-through X-ray look of the meshes) and *Volume* (true semi-transparent volume rendering of the scan data, no mesh needed).
- **2D images**: single pictures such as X-rays (DX/CR), which have no slices and cannot become 3D, can be placed as image planes in real size.

Use the planes for detail and the mesh or volume for spatial navigation.

## 2. Requirements and installation

- Blender 4.2 or newer. Tested with 4.2 LTS, 4.3, 4.4, 4.5 LTS, 5.1, 5.2 LTS and 5.3 alpha.
- Nothing else to install: the DICOM reader (pydicom) and the decoders for compressed DICOM (pylibjpeg with libjpeg and OpenJPEG) are bundled, and Blender installs the right ones for your system. Decoders are included for Windows x64, macOS Apple Silicon, macOS Intel (Blender 4.2-5.0) and Linux x64. On Windows ARM only uncompressed DICOM can be read.

**From the Blender Extensions platform:** in Blender open **Edit > Preferences > Get Extensions**, search for *Radiological Image Visualizer* and click **Install**. Blender picks the right package for your system and keeps it up to date.

**From this repository:**

1. Download `radiological_image_visualizer-1.0.1.zip` from the [Releases](https://github.com/smice-art/Radiological-Image-Visualizer/releases) page. This one file works on all systems.
2. In Blender open **Edit > Preferences > Get Extensions**.
3. Click the arrow menu at the top right and choose **Install from Disk...**
4. Select the downloaded zip file and confirm. An installed older version is replaced; restart Blender afterwards so the new code is loaded cleanly.
5. Open the 3D Viewport, press `N` for the sidebar and open the **RIV** tab.

At the top of the panel is the medical disclaimer and the status of the loaded volume. Below it come the sections: Cameras, 1. Load Data, 2. Slice Planes, 3. Surface Mesh, 4. Display (with Transfer Function), 5. 2D Images and About. Click the small arrow in front of a section title to fold it; Blender remembers which sections are open. Ctrl+click on an arrow folds all other sections. Sections that need a loaded volume (Cameras, 2-4) only appear after **Load Series**; *5. 2D Images* only appears when 2D images were found or imported. **Remove** sits at the end of *1. Load Data*; *About* shows version, author and website, with buttons for the source code and for reporting a bug.

## 3. Quick start

1. Choose the folder that contains the DICOM files and click **Scan Folder**. Subfolders are searched too.
2. Select a series in the list. Each entry shows series number, description, modality, slice count, image size and slice spacing (dz).
3. Click **Load Series**. Three slice planes appear in the 3D view, centred at the origin and scaled to real size, plus three cameras looking along X, Y and Z.
4. Move the **Axial**, **Coronal** and **Sagittal** sliders and adjust **Level** and **Window** until the image looks right.
5. Pick a **Tissue** preset or set a **Threshold**, then click **Create Mesh**. The default smoothing gives a clean surface.
6. Hide or delete the planes if you only want the mesh, or keep both and cut through the mesh with the planes.
7. Optional: under **4. Display** switch to **Glass** or **Volume**.

The model is placed in DICOM patient coordinates: head toward +Z, patient looking toward -Y. For a standard supine scan, Numpad 1 (Front view) shows the patient from the front.

## 4. Controls reference

### 4.1 Load data

| Control | What it does |
|---|---|
| **Folder** | Folder with the DICOM files. Note: the path is stored in the .blend file and may contain names. |
| **Subfolders** | Also search all subfolders. |
| **Scan Folder** | Reads only the file headers (fast) and lists all series with at least 2 slices. Compressed series show their format at the end of the entry (JPEG, JPEG-LS, JPEG2000, RLE), and a line below the list tells whether a decoder is available. |
| **Load Reduction** | Shrinks the volume while loading: 1 = full detail, 2 = 1/8 of the voxels, 3 = 1/27, 4 = 1/64. Saves memory and speeds up everything. Affects slice images and mesh. |
| **Scale** | Blender units per millimetre. 0.001 = real size with scene unit metres. 0.01 = 10x larger, 1 = 1000x larger. Takes effect on the next load. |
| **Set Color Management to 'Standard'** | Sets the scene view transform to Standard so slice images show unaltered grey values (AgX and Filmic change them). |
| **Load Series** | Reads the selected series and creates the three slice planes. |
| **Remove** | Frees the loaded volume from memory and deletes everything the add-on created (planes, meshes, volume object and its .vdb file, cameras, 2D image planes, RIV_Light). |

### 4.1b Cameras

**Load Series** also creates three cameras framed on the volume. The **Camera** field (click the arrows or drag) switches between them; the chosen one becomes the scene camera and the viewport looks through it.

| Camera | Looks along | View |
|---|---|---|
| 1 | X | Side view from the patient's left (sagittal) |
| 2 | Y | Front view, face to the camera (coronal) |
| 3 | Z | Top view from the head, face up (axial) |

| Control | What it does |
|---|---|
| **Ortho** | On (default): orthographic, no perspective distortion, like the slice images. Off: normal perspective camera. |
| **Scan Axes / Patient Axes** | *Scan Axes* (default): the cameras look straight along the slice grid, perpendicular to the slices. MRI slabs are often tilted by 10-20°; with this setting they appear straight and match the slice planes. *Patient Axes*: exactly along the body/world axes. For CT without gantry tilt both are identical. |
| **Recreate Cameras** | Appears if the cameras were deleted. |

Your own cameras are never changed. Press Numpad 0 to look through the scene camera again after orbiting away; switching with the arrows does this automatically.

### 4.2 Slice planes

All three sliders run from 0 (first slice) to 1 (last slice). The directions below assume a standard supine scan; for other positioning they can be reversed.

| Slider | 0 | 0.5 | 1 |
|---|---|---|---|
| **Axial** (horizontal cut) | lowest slice (feet) | middle | highest slice (head) |
| **Coronal** (front-to-back cut) | front (belly, face) | middle | back |
| **Sagittal** (left-to-right cut) | patient's right side | middle | patient's left side |

Example: with 300 axial slices of 1 mm, a value of 0.5 is about slice 150, roughly 15 cm above the start of the scan.

### 4.3 Window, Level and Contrast (slice images only)

Level is the centre of the displayed value range and Window is its width. Values below Level - Window/2 are shown black, values above Level + Window/2 white. All settings in this section affect only the slice images, never the mesh.

| Control | What it does |
|---|---|
| **Preset** | Typical Level/Window pairs (table below). Changing Level or Window by hand switches it to Custom. |
| **Auto** | Sets Level/Window from the value distribution inside the body (2nd to 98th percentile). Good starting point, especially for MRI. |
| **Level / Window** | As described above. |
| **Contrast** | S-curve on top of Level/Window. Positive = edges and mid-tones pop, negative = softer. 0 = linear. |
| **Brightness** | Brightens or darkens the mid-tones without clipping black or white (gamma). 0 = unchanged. |
| **Invert** | Bright = low values. |
| **Reset** | Sets Contrast, Brightness and Invert back to neutral. |

| Preset | Level | Window | Effect |
|---|---|---|---|
| Soft Tissue | 40 | 400 | Standard view |
| Heart / Vessels | 150 | 700 | Contrast-filled blood bright, heart muscle grey |
| Brain | 40 | 80 | Very high contrast in a narrow range |
| Lung | -600 | 1500 | Lung tissue visible |
| Bone | 400 | 1800 | Bone structure visible, soft tissue nearly grey |

A smaller Window gives more contrast but a narrower range; a larger Window shows more but looks flatter. Level moves which range is visible. These values are for CT; for MRI the values are relative and the add-on suggests a sensible range automatically when loading.

### 4.4 Surface mesh

| Control | What it does |
|---|---|
| **Tissue** | Preset for threshold and material. Bone = about 300 HU (ivory, solid). Heart / Vessels = about 200 HU (red; contrast-filled chambers and vessels). Skin = about -300 HU (semi-transparent). Custom = your own value, for example for MRI. |
| **Threshold** | The surface is built where values rise above this level (HU for CT). It selects which tissue forms the surface, not how detailed it is. See the table below. A red note appears if the value lies outside the data of this scan. |
| **Suggest** | Proposes a threshold from the value distribution of this scan (Otsu method: splits background and tissue) and switches to Custom. The quickest start for MRI. |
| **Mesh Reduction** | Mesh detail: 1 = full resolution, 2 = about 1/4 of the polygons, 3 = about 1/9, 4 = about 1/16. |
| **Close Ends** | On: the surface is capped where the volume ends, so the object looks solid. Off: open at the cut faces, you can look inside. |
| **Noise Filter (mm)** | Removes scanner noise from the volume *before* the surface is built. This is what makes the mesh smooth. 0.5-1 mm keeps small vessels, 1.5-2 mm gives very smooth organ shapes, 0 = raw grainy surface. Default 0.8. |
| **Edge-Preserving** (checkbox below it) | Uses an edge-preserving filter (anisotropic diffusion) first: noise inside tissues is smoothed strongly, boundaries stay sharp, so more thin vessels survive. Takes about 5-15 s on a full-resolution scan. |
| **Relax** | Smooths the remaining voxel steps on the mesh itself (Taubin smoothing, no shrinking). Each vertex stays inside its voxel, so no detail is invented or lost. Default 10. |
| **Islands** | Deletes loose parts smaller than this percentage of the largest part (noise specks, table fragments). Default 1 %. |
| **Create Mesh** | Builds the mesh. It is not live: press the button again after changing a setting. Every click creates a new object with the threshold in its name. |

**What each threshold gives you (CT)**

| Threshold (HU) | Result |
|---|---|
| -300 | Body surface (air/skin boundary). |
| 0 to 100 | Similar to the skin surface, but more sensitive to noise and the scanner table. |
| 150 to 250 | Contrast-filled vessels and the beginning of bone. |
| 300 | Complete bone including spongy bone; thin bones stay intact. |
| 500 to 700 | Only dense cortical bone: slimmer shapes, thin areas tear open. |
| above 1000 | Only metal, teeth and very dense bone. |

Rule of thumb: a lower threshold looks more inflated and picks up more noise; a higher threshold looks slimmer and loses thin structures.

**Why is my surface bumpy?** Usually not because of too few polygons but because the threshold lies in the middle of a tissue's own value range. Example heart CT: heart muscle is about 100-150 HU, contrast-filled blood about 250-350 HU. At 100 HU the surface runs *through* the muscle and follows the noise (foam look). At 180-200 HU it runs along the clean boundary between blood and muscle. Choose a threshold between two tissues, not inside one. Look at the slice images with the Heart / Vessels preset to see the ranges.

**Do not use Subdivision Surface on these meshes.** With millions of polygons it multiplies the mesh by 4 per level and can crash Blender (the panel shows a warning). Use Noise Filter and Relax instead; Shade Smooth is applied automatically.

### 4.5 The two Reduction sliders

|  | Load Reduction | Mesh Reduction |
|---|---|---|
| Applies when | Loading the series | Creating a mesh |
| Affects | Slice images and mesh | Mesh only |
| Level 1 | All voxels | Full resolution, very many polygons |
| Level 2 | 1/8 of the voxels | About 1/4 of the polygons |
| Levels 3 / 4 | 1/27 / 1/64 of the voxels | About 1/9 / 1/16 of the polygons |
| Use it to | Save memory, speed up everything | Keep the mesh light and fast |

### 4.6 Display

Three buttons switch the view. Nothing is lost when switching; the slice planes stay visible in every mode. Glass and Volume need **Material Preview** or **Rendered** shading.

| Mode | What you see | Controls |
|---|---|---|
| **Surface** | All surface meshes created by the add-on, with their normal materials. | - |
| **Glass** | The meshes as glowing, see-through X-ray glass. Additive blending, so it is fast and has no sorting errors even with millions of polygons. | Color, Brightness, Edge Focus (higher = only silhouettes glow), Core (glow of surfaces facing you) |
| **Volume** | True volume rendering of the scan itself (OpenVDB volume object), semi-transparent, no threshold needed. Works in EEVEE (fast) and Cycles (best quality). | Glow/Lit, Transfer preset, Low/High, Opacity, Brightness, colour and opacity curves - all live. Reduction + Build/Rebuild |

Volume details:

- **Glow / Lit**:
  - *Glow* (default): the volume shines by itself (emission + absorption, the classic medical volume rendering look). Independent of lights, fast and noise-free, looks the same in EEVEE and Cycles.
  - *Lit*: the volume is lit by lamps and the world (Principled Volume with light scattering and soft shadows inside the tissue). Much more photo-real, especially in **Cycles**; in EEVEE it is a soft preview only. If the scene has no light, a sun called *RIV_Light* is added. *Anisotropy* sets the scattering direction (positive = glowing rim against the light), *Self-Glow* adds a little self-illumination so the dark side stays readable.
- **Transfer Function** (box in the panel): two curves over the range Low to High.
  - *Colors*: which colour each value gets.
  - *Opacity*: how visible each value is; black = invisible, white = fully dense.
  - Both are edited directly in the panel (click a stop, drag it, change its colour, add stops with +). Several peaks in the Opacity curve show several tissues at once and hide what is in between. Example: the preset *Vessels (no bone)* shows contrast-filled vessels and hides heart muscle (below) AND bone (above).
  - Choosing a preset replaces both curves.

- The first switch to Volume builds the volume object automatically (about 1 s). **Rebuild** is needed only after changing **Reduction** (2 = recommended for EEVEE, 1 = full detail for Cycles).
- Transfer presets: *Heart / Vessels* (100-700 HU, heart muscle hidden, blood red, bone white), *Vessels (no bone)* (150-600 HU, two-sided opacity), *Bone* (150-1200 HU), *Soft Tissue* (-250-300 HU), *X-Ray* (grey ramp). Low/High stretch the curves over a value range: below Low the first stop applies, above High the last stop.
- Volumes built with version 0.2.x have the old material: press **Rebuild** once to get Glow/Lit and the curve editor.
- The volume is stored as a .vdb file in a folder **riv_cache** next to the saved .blend file. If the .blend is not saved yet, it goes to a temporary folder and is lost when Blender quits: save first, then build. The .vdb contains only voxel values, no patient data.
- Creating a new mesh while in Volume mode switches back to Surface, so the new mesh is visible.

### 4.7 2D images (X-ray and other single pictures)

X-rays (modality DX or CR), mammograms and similar pictures are single 2D projections: everything lies on top of each other and there are no slices, so no volume, mesh or 3D view can be computed from them. **Scan Folder** recognises them and lists them in section **5. 2D Images** (series number, description or view such as AP/Lateral, modality, pixel size and real size in cm).

| Control | What it does |
|---|---|
| **Import Selected / Import All** | Creates an upright picture plane in real size (from the pixel spacing in the file) for the selected or all images. Planes are placed side by side, to the right of a loaded volume. The front faces the Front view (Numpad 1). |
| **Level / Window** | Brightness/contrast of the active image plane (select it in the viewport). Live, stored with the object. The start values come from the file. |
| **Invert** | Bone dark instead of bright. |
| **Reset** | Back to the values from the file. |

- The image is stored losslessly as a 16-bit PNG inside the .blend file (a few MB per X-ray) and survives saving; no rescan is needed later.
- MONOCHROME1 images (common for X-rays) are converted so that bone appears bright, as in a normal viewer.
- If a file has no pixel size, 0.1 mm per pixel is assumed (a message tells you).

## 5. Typical workflows

- **Skull or skeleton (CT)**: Tissue = Bone, Threshold 300, Mesh Reduction 2. Increase detail with Mesh Reduction 1 once the result looks right.
- **Skin and bone together (CT)**: create a Skin mesh first, then a Bone mesh. The skin material is semi-transparent so the bone shows through. Use the planes to cut into both.
- **Heart / vessels with contrast (CT angiography)**: Tissue = Heart / Vessels (200 HU), Noise Filter 0.8, Edge-Preserving on, Mesh Reduction 1. Adjust the threshold in steps of 20 to 30. For a quick overview without any mesh use Display = Volume with the Heart / Vessels preset.
- **MRI**: Tissue = Custom. The add-on suggests a start value when loading (also available any time with **Suggest**). MRI values have no fixed meaning like HU, so the CT presets Bone/Skin/Heart do not apply (the panel warns). Look at the slice images first to see which grey values belong to the tissue you want, and set the threshold between tissue and background.
- **Compare variants**: create meshes at several thresholds. Each becomes its own object; toggle their visibility in the Outliner.
- **Very smooth presentation model**: Noise Filter 1.5-2 mm, Relax 20, Islands 2 %.

## 6. Tips and good to know

- Slice images and materials need **Material Preview** or **Rendered** shading. The add-on switches the viewport to Material Preview when you load a series.
- Slice thickness: the add-on reads it from the headers and shows the slice spacing in the series list. Thin slices (about 1 mm or less) give smooth 3D results; thick slices (3 to 5 mm) produce visible steps.
- Old meshes are not removed automatically. Delete the ones you no longer need, or use **Remove** to clear everything the add-on created (including the volume object and its .vdb file).
- The loaded volume lives in memory only. After reopening a .blend file, scan and load the series again to use the sliders, create meshes or build a volume. Existing meshes and planes stay in the file.
- Create Mesh supports Undo (Ctrl+Z). Each undo step of a large mesh needs memory; lower Edit > Preferences > System > Undo Steps if memory is tight.
- The planes are generated images. If you need them to survive saving the .blend file, pack them (Image Editor > Image > Pack).
- Loading and mesh creation block the interface for a moment. With a 512 x 512 x 280 heart CT, Create Mesh with default smoothing takes about 5-8 s at full resolution (about 10 s more with Edge-Preserving). If SciPy happens to be installed in Blender's Python it is used automatically and is faster; it is not required.
- To remove the scanner table or other floating pieces: select the mesh, enter Edit Mode, press `P` and choose **By Loose Parts**, then delete the unwanted pieces.

## 7. Troubleshooting

| Problem | What to try |
|---|---|
| No series found | Check the folder. If it contains only X-rays or other single pictures, they appear under *5. 2D Images* instead (they cannot be shown in 3D). Multi-frame DICOM files and files without a standard DICOM header are skipped; the info line shows how many multi-frame files were found. If the folder only contains viewer software (help files, folders like *Lut*, *weasis*, *jre*) but no image folder (often *DICOM*, *IMAGES* or *DATA*), the copy of the CD is incomplete: copy the whole CD again. |
| 'Cannot read image data' | The message names the compression. JPEG, JPEG Lossless, JPEG-LS, JPEG2000 and RLE are supported through the bundled decoders. If the line below the series list says 'no decoder installed', your system is not covered (for example Intel Mac with Blender 5.1+) or the installation was incomplete: reinstall the add-on, or export the series uncompressed. A few files with non-standard JPEG streams can still fail. |
| 'Not enough memory' | Set Load Reduction to 2 and load again. |
| Slice images look too bright or dull | Enable 'Set Color Management to Standard' (or set View Transform to Standard in Render Properties) and use Material Preview. |
| Planes are grey or empty | Adjust Level and Window. For MRI the useful range differs from CT. |
| Mesh is empty or 'No surface: threshold ... is below/above all values' | The threshold lies outside the data of this scan; the message shows the data range and a suggested value. Typical case: a CT value (e.g. -300 from the Skin preset) on an MRI. Click **Suggest**. Selecting Custom also replaces such an out-of-range value automatically. |
| Mesh is too heavy | Raise Mesh Reduction to 3 or 4, or load with Load Reduction 2. A Decimate modifier also works. |
| Mesh looks foamy or bumpy | The threshold is inside a tissue's value range (see 4.4). Move it between two tissues; raise Noise Filter; turn on Edge-Preserving. |
| Blender crashes after adding Subdivision Surface | Expected with multi-million polygon meshes. Remove the modifier; use Noise Filter/Relax instead. |
| Glass or Volume looks empty | Switch the viewport to Material Preview or Rendered. For Volume try lowering Low and raising Opacity. |
| Volume missing after reopening the file | The .blend was not saved when the volume was built (temporary .vdb). Load the series and press Build Volume again after saving. |
| 'This Blender build has no OpenVDB Python module' | Rare custom builds lack OpenVDB. Use the Glass mode instead. |
| Warning: uneven slice spacing | The series has gaps or irregular positions. The geometry is then only approximate. |
| Warning: gantry tilt | Slices are stacked at an angle. The add-on shows the volume sheared, which is geometrically correct. |
| Warning: slices ignored at identical position | The series contains several phases or echoes at the same location. Only the first slice per position is used. |

## 8. Reference: typical Hounsfield values (CT)

Approximate values; they vary with scanner, protocol and contrast agent.

| Material | HU |
|---|---|
| Air | -1000 |
| Lung tissue | -900 to -500 |
| Fat | -120 to -60 |
| Water | 0 |
| Blood, soft tissue, muscle | 30 to 80 |
| Contrast-filled vessels | 150 to 400 |
| Spongy bone | 200 to 400 |
| Dense cortical bone | 500 to 1500 and above |
| Metal, dental fillings | above 2000 (may saturate) |


## F1. Limitations of version 1.0.1

- Multi-frame (enhanced) DICOM files are skipped.
- Compressed DICOM is decoded on Windows x64, macOS Apple Silicon, macOS Intel (up to Blender 5.0) and Linux x64. Other systems need uncompressed data.
- Lit volume rendering is a soft preview in EEVEE; use Cycles for final images.
- The mesh is created on demand, not live while you drag the threshold (the Volume display mode is live).
- The volume object cannot be cut by the slice planes yet.
- Islands removes small loose parts automatically; large pieces such as the scanner table may remain. Use Loose Parts to clean up.
- The interface blocks briefly during loading and mesh creation.

## F2. Privacy and disclaimer

- The add-on does not store patient names, IDs or dates in the .blend file. Only the series description is shown in the list.
- The chosen folder path is stored in the .blend file and can contain names. Clear the Folder field before sharing a file.
- **This is a visualization tool. It is not a medical device and must not be used for diagnosis or treatment decisions.**

## F3. License and credits

- Radiological Image Visualizer by Claudio - https://superhivemarket.com/creators/smice
- Source code: https://github.com/smice-art/Radiological-Image-Visualizer
- Bug reports and support: https://github.com/smice-art/Radiological-Image-Visualizer/issues
- License: GNU General Public License v3.0 or later (GPL-3.0-or-later), see the file `LICENSE`.
- Bundled third-party packages (pydicom, pylibjpeg, pylibjpeg-libjpeg, pylibjpeg-openjpeg) and their licenses are listed in [`source/THIRD_PARTY_LICENSES.md`](source/THIRD_PARTY_LICENSES.md).
- DICOM® is the registered trademark of the National Electrical Manufacturers Association (NEMA) for its standards publications relating to digital communications of medical information. This project is not affiliated with or endorsed by NEMA.

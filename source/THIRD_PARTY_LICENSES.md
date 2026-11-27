# Third-party components

Radiological Image Visualizer is licensed under the GNU General Public License v3.0 or later
(see `LICENSE`). It bundles the following third-party Python packages as wheels in
the `wheels` folder. Blender installs only the wheels that match your system. Each
wheel contains its full license text in its `*.dist-info` folder.

All of these licenses are compatible with GPL-3.0-or-later.

| Package | Version | License | Source code |
|---|---|---|---|
| pydicom | 3.0.2 | MIT. Its private dictionary is derived from GDCM (BSD-3-Clause) | https://github.com/pydicom/pydicom |
| pylibjpeg | 2.1.0 | MIT | https://github.com/pydicom/pylibjpeg |
| pylibjpeg-libjpeg | Python 3.11: 2.5.0 (Linux 2.4.0); Python 3.13: 2.4.0 (Linux 2.3.0) | GPL-3.0, includes libjpeg by Thomas Richter (GPL-3.0) | https://github.com/pydicom/pylibjpeg-libjpeg, https://github.com/thorfdbg/libjpeg |
| pylibjpeg-openjpeg | Python 3.11: 2.6.0; Python 3.13: 2.5.0 | MIT, includes OpenJPEG (BSD-2-Clause) | https://github.com/pydicom/pylibjpeg-openjpeg, https://github.com/uclouvain/openjpeg |

The source code of every bundled package is available at the addresses above and as
source distributions on PyPI (https://pypi.org).

NumPy, SciPy (optional) and OpenVDB are not bundled; they come with Blender or the
user's own Python installation.

## Trademark

DICOM® is the registered trademark of the National Electrical Manufacturers Association
(NEMA) for its standards publications relating to digital communications of medical
information. This project is not affiliated with or endorsed by NEMA.

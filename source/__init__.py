# SPDX-FileCopyrightText: 2026 Claudio
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Radiological Image Visualizer - explore CT and MRI series in 3D.

Metadata (name, version, maintainer, license, website) lives in blender_manifest.toml.
"""

from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()

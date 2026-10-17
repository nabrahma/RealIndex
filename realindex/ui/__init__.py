# SPDX-License-Identifier: GPL-3.0-or-later
from . import panels, quick_place

_modules = (quick_place, panels)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()

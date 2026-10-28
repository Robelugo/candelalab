"""Prueba de interfaz: abre Blender, crea un cascarón, muestra el panel con todos
los subpaneles abiertos, guarda una captura y cierra. Los errores de dibujo de
los paneles aparecen como Traceback en la salida.

    blender --factory-startup --python tests/blender_ui_test.py -- captura.png
"""

import math
import os
import sys

import bpy
from mathutils import Euler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import candelalab  # noqa: E402
from candelalab import panels  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(ROOT, "captura.png")
PARAMS = dict(edge_mode='RULED', analysis='SLOPE', beam_ymin=True, beam_xmax=True, support_type='COLUMN', rise=3.5)

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.ui_scale = 0.8
for cls in panels.classes:  # abrir todos los subpaneles para que se dibujen
    if hasattr(cls, "bl_options"):
        cls.bl_options = set(cls.bl_options) - {'DEFAULT_CLOSED'}
    if hasattr(cls, "bl_category"):
        # La pestaña activa de la barra lateral no se puede cambiar desde Python:
        # sólo para la captura, el panel se muestra en la pestaña por defecto.
        cls.bl_category = "Item"
bpy.utils.unregister_class(bpy.types.VIEW3D_PT_transform)
candelalab.register()


def _view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return win, area


def setup():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    win, area = _view3d()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.candela.add_hypar()
    obj = bpy.context.view_layer.objects.active
    for k, v in PARAMS.items():
        setattr(obj.candela, k, v)
    space = area.spaces.active
    space.show_region_ui = True
    space.shading.color_type = 'VERTEX'
    space.overlay.show_floor = True
    r3d = space.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_rotation = Euler((math.radians(64), 0, math.radians(32))).to_quaternion()
    r3d.view_location = (0.0, 0.0, 2.5)
    r3d.view_distance = 26.0
    return None


def shot():
    win, area = _view3d()
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot(filepath=OUT)
    print("CAPTURA", OUT, flush=True)
    os._exit(0)  # salida inmediata: no escribe quit.blend de recuperación


bpy.app.timers.register(setup, first_interval=1.0)
bpy.app.timers.register(shot, first_interval=5.0)

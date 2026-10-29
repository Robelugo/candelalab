"""CandelaLab: diseñador paramétrico de cascarones laminares de concreto para Blender.

Arquitectura
------------
core/          Núcleo geométrico puro (sin bpy): ecuaciones, mallado, métricas,
               comprobaciones y exportación. Fuente única de verdad.
props.py       Parámetros como PropertyGroup en Object.candela.
build.py       Adaptador: escribe el resultado del núcleo en datablocks.
operators.py   Acciones (crear, regenerar, variantes, exportar...).
panels.py      Interfaz en la barra lateral (N) > CandelaLab.
instancing.py  Retículas grandes: instanciación con Geometry Nodes.
"""

bl_info = {
    "name": "CandelaLab",
    "author": "CandelaLab",
    "version": (0, 13, 0),
    "blender": (4, 2, 0),
    "location": "Vista 3D > Barra lateral (N) > CandelaLab · Añadir > Malla > Hypar (CandelaLab)",
    "description": "Diseño paramétrico de cascarones laminares de concreto: hypars y bóvedas radiales de hypars",
    "category": "Add Mesh",
}

from . import build, operators, panels, props  # noqa: E402

_modules = (props, operators, panels)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
    build._report_cache.clear()

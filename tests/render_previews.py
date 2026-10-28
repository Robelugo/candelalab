"""Renderiza vistas previas de las bóvedas radiales y los paraguas sin abrir la interfaz.

    blender --background --factory-startup --python tests/render_previews.py -- carpeta_salida [prefijo]

Con un prefijo (p. ej. «cimbra») sólo se renderizan las vistas cuyo nombre empieza así.

Usa Workbench (rápido, sin luces que configurar).
"""

import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import candelalab  # noqa: E402
from candelalab import build  # noqa: E402
from candelalab.core.grid import PRESETS as GRID  # noqa: E402
from candelalab.core.radial import PRESETS as RADIAL  # noqa: E402
from candelalab.core.umbrella import PRESETS as UMBRELLA  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = ARGS[0] if ARGS else os.path.join(ROOT, "docs")
ONLY = ARGS[1] if len(ARGS) > 1 else ""
os.makedirs(OUT, exist_ok=True)


def setup_scene():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 1400, 900
    scene.render.film_transparent = False
    shading = scene.display.shading
    shading.light = 'STUDIO'
    shading.color_type = 'MATERIAL'
    shading.show_cavity = True
    shading.show_shadows = True
    shading.background_type = 'VIEWPORT' if hasattr(shading, "background_type") else shading.background_type
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (0.86, 0.87, 0.89)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.lens = 40
    # suelo
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, -0.01))
    floor = bpy.context.active_object
    mat = bpy.data.materials.new("Suelo")
    mat.diffuse_color = (0.75, 0.76, 0.78, 1.0)
    floor.data.materials.append(mat)
    return scene, cam


def frame(cam, radius, height, elev_deg=24.0, azim_deg=-28.0, dist=2.7, target_frac=0.35):
    target = Vector((0.0, 0.0, target_frac * height))
    r = dist * radius
    e, a = math.radians(elev_deg), math.radians(azim_deg)
    cam.location = target + Vector((r * math.cos(e) * math.cos(a), r * math.cos(e) * math.sin(a), r * math.sin(e)))
    cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()


def main():
    candelalab.register()
    scene, cam = setup_scene()
    jobs = [(f"boveda_{k.lower()}", "RADIAL", v[0], v[2], v[2]["radial_radius"] / 14) for k, v in RADIAL.items()]
    jobs += [(f"paraguas_{k.lower()}", "UMBRELLA", v[0], v[2], 0.5) for k, v in UMBRELLA.items()]
    module = {"res_x": 8, "res_y": 8, "umbrella_column": True, "support_type": "COLUMN", "support_size": 0.4}
    jobs += [(f"reticula_{k.lower()}", "UMBRELLA", v[0], {**module, **v[2]}, 0.6) for k, v in GRID.items()]
    big = {**module, **GRID["INVERTED"][2], "grid_count_x": 14, "grid_count_y": 10, "grid_instancing": "INSTANCES"}
    jobs.append(("reticula_instancias", "UMBRELLA", "Retícula instanciada", big, 1.25))
    # Cimbra: el cascarón se oculta para ver tablas, vigas y puntales.
    form = {"formwork_enabled": True, "show_rulings": False, "show_supports": False, "res_x": 8, "res_y": 8}
    # Desde abajo y sin puntales se ven las vigas (una familia) cruzando las tablas (la otra).
    jobs.append(("cimbra_paraguas", "UMBRELLA", "Cimbra del paraguas",
                 {**UMBRELLA["INVERTED"][2], **form, "formwork_prop_spacing": 0.0}, 0.5,
                 {"hide_shell": True, "elev": -24.0, "target": 0.85, "hide_floor": True}))
    jobs.append(("cimbra_hypar", "HYPAR", "Cimbra de la hypar", {"rise": 4.0, "support_layout": "NONE", **form},
                 0.5, {"hide_shell": True}))
    # Rigidizadores vistos desde abajo
    stiff = {"rib_creases": True, "rib_width": 0.2, "rib_depth": 0.3, "show_rulings": False, "res_x": 12, "res_y": 12}
    below = {"elev": -22.0, "target": 0.8, "hide_floor": True}
    jobs.append(("rigidizadores_paraguas", "UMBRELLA", "Paraguas con nervios",
                 {**UMBRELLA["INVERTED"][2], **stiff, "show_supports": True, "support_type": "COLUMN"}, 0.5, below))
    jobs.append(("rigidizadores_boveda", "RADIAL", "Cúpula con nervios y viga de borde",
                 {**RADIAL["DOME"][2], **stiff, "beam_free_edge": True, "beam_width": 0.25, "beam_depth": 0.35},
                 0.5, below))
    jobs.append(("capitel_paraguas", "UMBRELLA", "Paraguas con capitel",
                 {**UMBRELLA["INVERTED"][2], **stiff, "res_x": 32, "res_y": 32, "show_supports": True,
                  "support_type": "COLUMN", "capital_enabled": True, "capital_radius": 1.8, "capital_extra": 0.25},
                 0.5, {"elev": -14.0, "target": 0.8, "hide_floor": True, "dist": 1.9}))
    floor = bpy.data.objects.get("Plane")
    for filename, kind, label, params, spacing, *opts in jobs:
        opt = opts[0] if opts else {}
        if not filename.startswith(ONLY):
            continue
        # Un paraguas está a 4–5 m del suelo: su sombra cae lejos y parece otro objeto.
        scene.display.shading.show_shadows = kind == "RADIAL"
        for o in [o for o in bpy.data.objects if build.is_shell(o)]:
            build.remove_all_children(o)
            bpy.data.objects.remove(o)
        obj = build.create_shell(bpy.context, name=label, params={"shell_type": kind, **params,
                                                                  "ruling_spacing": spacing,
                                                                  "ruling_radius": spacing / 32})
        rep = build.report_of(obj)
        height = rep["geometry"]["z_concrete_max"]
        if rep.get("grid"):  # con instancias la malla del objeto es un solo módulo: se usa la huella
            reach = 0.5 * math.hypot(*rep["grid"]["footprint"])
        else:
            reach = max(math.hypot(v.co.x, v.co.y) for v in obj.data.vertices)  # incluye vuelos y esquinas
        frame(cam, reach, height, elev_deg=opt.get("elev", 24.0), target_frac=opt.get("target", 0.35),
              dist=opt.get("dist", 2.7))
        obj.hide_render = opt.get("hide_shell", False)
        floor.hide_render = opt.get("hide_floor", False)
        scene.render.filepath = os.path.join(OUT, f"{filename}.png")
        bpy.ops.render.render(write_still=True)
        print("RENDER", scene.render.filepath)
    candelalab.unregister()


main()

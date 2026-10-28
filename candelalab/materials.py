"""Materiales de visualización (se crean una vez y se reutilizan)."""

import bpy

PALETTE = {
    "concrete": ("CandelaLab · Concreto", (0.62, 0.61, 0.58, 1.0), 0.85),
    "beam": ("CandelaLab · Viga", (0.50, 0.49, 0.46, 1.0), 0.85),
    "rib": ("CandelaLab · Nervio", (0.46, 0.45, 0.43, 1.0), 0.85),
    "ruling_a": ("CandelaLab · Generatriz A", (0.80, 0.22, 0.12, 1.0), 0.5),
    "ruling_b": ("CandelaLab · Generatriz B", (0.10, 0.36, 0.80, 1.0), 0.5),
    "normal": ("CandelaLab · Normal", (0.15, 0.62, 0.30, 1.0), 0.5),
    "support": ("CandelaLab · Apoyo", (0.18, 0.18, 0.20, 1.0), 0.6),
    "board": ("CandelaLab · Tabla", (0.80, 0.64, 0.42, 1.0), 0.8),
    "joist": ("CandelaLab · Viga de cimbra", (0.58, 0.40, 0.24, 1.0), 0.8),
    "prop": ("CandelaLab · Puntal", (0.46, 0.34, 0.24, 1.0), 0.8),
}


def get(key: str) -> bpy.types.Material:
    name, rgba, roughness = PALETTE[key]
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba  # color en el modo Sólido del visor
    mat.roughness = roughness
    if not getattr(mat, "use_nodes", True):  # Blender < 5.0
        mat.use_nodes = True
    if mat.node_tree is not None:
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                node.inputs["Base Color"].default_value = rgba
                node.inputs["Roughness"].default_value = roughness
    return mat


def assign(data, keys: list) -> None:
    """Deja en `data.materials` exactamente los materiales indicados, en orden."""
    mats = [get(k) for k in keys]
    if list(data.materials) == mats:
        return
    data.materials.clear()
    for m in mats:
        data.materials.append(m)

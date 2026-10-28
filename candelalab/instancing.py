"""Instanciación de retículas con Geometry Nodes (capa Blender).

El núcleo entrega UN módulo y sus colocaciones (core/grid.py). Aquí se crea:

* un objeto hijo «Colocaciones»: vértices sueltos (invisibles en modo objeto),
  uno por módulo, con el atributo de punto `candela_scale` = (±1, ±1, 1);
* un grupo de nodos compartido, «CandelaLab · Instancias»:
      Object Info (Colocaciones, relativo) → Instance on Points
      Instance = geometría del objeto, Scale = atributo candela_scale
  La escala −1 refleja el módulo (determinante −1: Blender dibuja bien las
  normales de instancias reflejadas);
* un modificador con ese grupo en el cascarón y en sus hijos instanciables
  (generatrices, normales y vigas). Las columnas NO se instancian: cada una
  llega al desplante y su longitud cambia con el escalonamiento.

La memoria y el tiempo de regeneración no dependen del número de módulos: el
núcleo calcula y Blender escribe un solo módulo más N puntos.
"""

import bpy

GROUP_NAME = "CandelaLab · Instancias"
GROUP_VERSION = 1
MODIFIER_NAME = "CandelaLab · Retícula"
POINTS_SOCKET = "Colocaciones"
SCALE_ATTR = "candela_scale"


def node_group():
    """Grupo de nodos de instanciación (se crea una vez por archivo)."""
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None and ng.get("candela_version") == GROUP_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP_NAME, 'GeometryNodeTree')
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket(POINTS_SOCKET, in_out='INPUT', socket_type='NodeSocketObject')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_out = nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    scale = nodes.new('GeometryNodeInputNamedAttribute')
    scale.data_type = 'FLOAT_VECTOR'
    scale.inputs["Name"].default_value = SCALE_ATTR
    inst = nodes.new('GeometryNodeInstanceOnPoints')
    for node, x, y in ((n_in, -600, 0), (info, -350, 150), (scale, -350, -150), (inst, -50, 0), (n_out, 200, 0)):
        node.location = (x, y)

    links.new(n_in.outputs[POINTS_SOCKET], info.inputs["Object"])
    links.new(info.outputs["Geometry"], inst.inputs["Points"])
    links.new(n_in.outputs["Geometry"], inst.inputs["Instance"])
    links.new(scale.outputs["Attribute"], inst.inputs["Scale"])
    links.new(inst.outputs["Instances"], n_out.inputs["Geometry"])
    ng["candela_version"] = GROUP_VERSION
    return ng


def _points_identifier(ng) -> str:
    for item in ng.interface.items_tree:
        if getattr(item, "in_out", None) == 'INPUT' and item.name == POINTS_SOCKET:
            return item.identifier
    raise RuntimeError("El grupo de instanciación no tiene la entrada de colocaciones.")


def write_placements(mesh, placements) -> None:
    """Un vértice por módulo en su desplazamiento, con la escala de reflexión."""
    mesh.clear_geometry()
    mesh.from_pydata([tuple(pl["offset"]) for pl in placements], [], [])
    attr = mesh.attributes.get(SCALE_ATTR) or mesh.attributes.new(SCALE_ATTR, 'FLOAT_VECTOR', 'POINT')
    flat = []
    for pl in placements:
        mx, my = pl["mirror"]
        flat += [-1.0 if mx else 1.0, -1.0 if my else 1.0, 1.0]
    attr.data.foreach_set("vector", flat)
    mesh.update()


def attach(obj, points_obj) -> None:
    """Instancia la geometría de `obj` en las colocaciones."""
    ng = node_group()
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None or mod.type != 'NODES':
        mod = obj.modifiers.new(MODIFIER_NAME, 'NODES')
    if mod.node_group != ng:
        mod.node_group = ng
    ident = _points_identifier(ng)
    if mod.get(ident) != points_obj:
        mod[ident] = points_obj
    obj.update_tag()


def detach(obj) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        obj.modifiers.remove(mod)


def is_instanced(obj) -> bool:
    return obj.modifiers.get(MODIFIER_NAME) is not None

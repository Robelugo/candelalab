"""Propiedades de Blender: el estado paramétrico vive en Object.candela.

Los nombres de las propiedades son las mismas claves que usa el núcleo
(core/hypar.py DEFAULTS), así que variantes, JSON e interfaz comparten un único
diccionario plano de parámetros.
"""

import json
import math
from contextlib import contextmanager

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import PropertyGroup

_suspend_depth = 0


@contextmanager
def updates_suspended():
    """Agrupa varios cambios de parámetros en una sola regeneración."""
    global _suspend_depth
    _suspend_depth += 1
    try:
        yield
    finally:
        _suspend_depth -= 1


def _on_change(self, context):
    if _suspend_depth:
        return
    obj = self.id_data
    if not isinstance(obj, bpy.types.Object) or obj.type != 'MESH' or not self.is_shell:
        return
    from . import build
    build.rebuild(obj)


def _on_result_change(self, context):
    """Cambiar el resultado mostrado sólo vuelve a colorear: no regenera el cascarón."""
    if _suspend_depth or not self.is_shell:
        return
    from . import build
    build.apply_fea_results(self.id_data)


_item_lists: dict = {}  # Blender exige conservar las cadenas de un enum dinámico


def _result_items(self, context):
    try:
        data = json.loads(self.fea_results_summary) if self.fea_results_summary else {}
    except ValueError:
        data = {}
    labels = [it["label"] for it in data.get("items", [])] or ["Sin resultados"]
    items = [(str(i), label, label) for i, label in enumerate(labels)]
    _item_lists[self.as_pointer()] = items
    return items


# Propiedades que no son parámetros de diseño (no van a variantes ni a JSON).
NON_PARAMETERS = {"rna_type", "name", "is_shell", "report_json", "variants", "variant_index", "fea_summary_json",
                  "fea_results_path", "fea_results_key", "fea_results_summary", "fea_results_legend",
                  "fea_export_key", "fea_result_item", "fea_result_quantity", "fea_result_show",
                  "fea_result_deformed", "fea_result_scale"}


def settings_to_dict(settings) -> dict:
    out = {}
    for prop in settings.bl_rna.properties:
        key = prop.identifier
        if key in NON_PARAMETERS or prop.type in {'POINTER', 'COLLECTION'}:
            continue
        out[key] = getattr(settings, key)
    return out


def apply_dict(settings, data: dict) -> list:
    """Aplica un diccionario de parámetros sin regenerar. Devuelve las claves ignoradas."""
    ignored = []
    props = settings.bl_rna.properties
    with updates_suspended():
        for key, value in data.items():
            if key in NON_PARAMETERS or key not in props:
                ignored.append(key)
                continue
            try:
                setattr(settings, key, value)
            except (TypeError, ValueError, AttributeError):
                ignored.append(key)
    return ignored


def _dist(name, default, desc, soft_min=None, soft_max=None, min=None, max=None, precision=3, step=10):
    kw = dict(name=name, default=default, description=desc, subtype='DISTANCE', unit='LENGTH',
              precision=precision, step=step, update=_on_change)
    for k, v in (("soft_min", soft_min), ("soft_max", soft_max), ("min", min), ("max", max)):
        if v is not None:
            kw[k] = v
    return FloatProperty(**kw)


def _angle(name, default_deg, desc, lo_deg, hi_deg):
    return FloatProperty(name=name, default=math.radians(default_deg), description=desc, subtype='ANGLE',
                         min=math.radians(lo_deg), max=math.radians(hi_deg), update=_on_change)


class CANDELA_PG_variant(PropertyGroup):
    name: StringProperty(name="Nombre", default="Variante")
    data: StringProperty(name="Parámetros (JSON)", default="{}")


class CANDELA_PG_shell(PropertyGroup):
    is_shell: BoolProperty(default=False, options={'HIDDEN'})
    shell_type: EnumProperty(
        name="Tipo",
        items=[('HYPAR', "Paraboloide hiperbólico", "Hypar individual sobre planta rectangular"),
               ('RADIAL', "Bóveda radial (cúpula)", "N hypars alrededor de una clave, unidos por limas rectas; "
                "planta circular o poligonal"),
               ('UMBRELLA', "Paraguas (4 hypars)", "Cuatro hypars de bordes rectos sobre una columna central")],
        default='HYPAR', update=_on_change)

    # --- Paraguas -------------------------------------------------------------------------
    umbrella_center_height: _dist("Altura de la columna", 4.0, "Altura del centro del paraguas (cabeza de columna)",
                                  soft_min=-20, soft_max=50)
    umbrella_edge_height: _dist("Altura del borde", 5.0, "Altura del perímetro (sin inclinación). Por encima de la "
                                "columna: paraguas invertido (limas valle); por debajo: normal (limas cumbrera)",
                                soft_min=-20, soft_max=50)
    umbrella_column: BoolProperty(name="Columna central", default=True, update=_on_change,
                                  description="Apoyo bajo el centro del paraguas")

    # --- Bóveda radial ----------------------------------------------------------------
    radial_count: IntProperty(name="Lóbulos", default=8, min=3, max=24, update=_on_change,
                              description="Número de paraboloides hiperbólicos alrededor de la clave")
    radial_plan: EnumProperty(
        name="Borde libre",
        items=[('CIRCLE', "Cilindro", "Planta circular: borde sobre la circunferencia (curva alabeada)"),
               ('POLYGON', "Plano vertical", "Planta poligonal: arcos parabólicos en planos verticales"),
               ('INCLINED', "Plano inclinado", "Tipo Los Manantiales: cada lóbulo se corta con un plano que pasa "
                "por sus dos apoyos y se inclina hacia afuera; el borde es una hipérbola plana y el lóbulo vuela "
                "sobre la línea de apoyos")],
        default='CIRCLE', update=_on_change)
    edge_overhang: _dist("Vuelo del borde", 3.0, "Con plano inclinado: distancia horizontal, sobre el eje del "
                         "lóbulo, entre la línea de apoyos y el vértice del borde", min=0.01, soft_max=20.0)
    radial_radius: _dist("Radio", 10.0, "Distancia en planta de la clave a cada apoyo", min=0.5, soft_max=40.0)
    crown_height: _dist("Altura de clave", 5.0, "Altura del centro", soft_min=-20, soft_max=50)
    edge_height: _dist("Altura de borde", 1.5, "Altura del vértice del borde libre (sobre el eje de cada lóbulo). Al subirla "
                       "los lóbulos se abomban y las limas pasan de cumbreras a valles", soft_min=-20, soft_max=50)
    support_height: _dist("Altura de apoyos", 0.0, "Altura de los extremos de las limas", soft_min=-20, soft_max=50)
    radial_supports: EnumProperty(
        name="Apoyos",
        items=[('GROINS', "En las limas", "Un apoyo al pie de cada lima"),
               ('CENTER', "Columna central", "Apoyo único bajo la clave (paraguas invertido)"),
               ('NONE', "Ninguno", "")],
        default='GROINS', update=_on_change)

    # --- Geometría ----------------------------------------------------------------
    definition: EnumProperty(
        name="Definición",
        items=[('PARAMS', "Alabeo", "Altura del centro, alabeo e inclinación"),
               ('CORNERS', "Esquinas", "Alturas de las cuatro esquinas (bordes rectos)")],
        default='PARAMS', update=_on_change)
    span_x: _dist("Luz X", 10.0, "Dimensión de la planta en X", min=0.1, soft_max=60.0)
    span_y: _dist("Luz Y", 10.0, "Dimensión de la planta en Y", min=0.1, soft_max=60.0)
    rise: _dist("Altura del centro", 2.5, "Altura z del centro de la superficie de referencia", soft_min=-20, soft_max=50)
    warp: _dist("Alabeo", 2.5, "Separación vertical máxima, en el contorno, entre la superficie y su plano "
                "tangente en el centro. Con bordes rectos es la subida de las esquinas. El signo invierte el alabeo",
                soft_min=-20, soft_max=20)
    edge_mode: EnumProperty(
        name="Bordes",
        items=[('RULED', "Rectos", "Los bordes son generatrices (θ = 0°)"),
               ('PRINCIPAL', "Parabólicos", "Bordes en los planos principales (θ = 45°)"),
               ('CUSTOM', "Ángulo", "Ángulo libre entre bordes y generatrices")],
        default='RULED', update=_on_change)
    edge_angle: _angle("Ángulo bordes/generatrices", 0.0, "θ entre los bordes de la planta y las generatrices", 0, 90)
    tilt_x: _angle("Inclinación X", 0.0, "Pendiente del plano medio en dirección X", -60, 60)
    tilt_y: _angle("Inclinación Y", 0.0, "Pendiente del plano medio en dirección Y", -60, 60)
    z_sw: _dist("SO (−X −Y)", 5.0, "Altura de la esquina −X −Y", soft_min=-20, soft_max=50)
    z_se: _dist("SE (+X −Y)", 0.0, "Altura de la esquina +X −Y", soft_min=-20, soft_max=50)
    z_ne: _dist("NE (+X +Y)", 5.0, "Altura de la esquina +X +Y", soft_min=-20, soft_max=50)
    z_nw: _dist("NO (−X +Y)", 0.0, "Altura de la esquina −X +Y", soft_min=-20, soft_max=50)

    thickness: _dist("Espesor", 0.04, "Espesor del cascarón", min=0.001, max=5.0, soft_max=0.5, step=1)
    thickness_mode: EnumProperty(
        name="Espesor medido",
        items=[('NORMAL', "Normal", "Perpendicular a la superficie: espesor real constante; "
                "las caras desfasadas NO son hypars exactas"),
               ('VERTICAL', "Vertical", "En dirección Z: ambas caras son hypars exactas; "
                "el espesor real varía con la pendiente")],
        default='NORMAL', update=_on_change)
    thickness_reference: EnumProperty(
        name="Superficie exacta",
        items=[('INTRADOS', "Intradós", "La cara inferior (cimbra) es la hypar exacta"),
               ('MID', "Media", "La superficie media es la hypar exacta"),
               ('EXTRADOS', "Extradós", "La cara superior es la hypar exacta")],
        default='INTRADOS', update=_on_change)

    output: EnumProperty(
        name="Salida",
        items=[('SOLID', "Sólido", "Sólido cerrado con espesor"),
               ('SURFACE', "Superficie", "Sólo la superficie de referencia (sin espesor)")],
        default='SOLID', update=_on_change)
    res_x: IntProperty(name="Divisiones X", default=32, min=1, max=512, soft_max=160, update=_on_change)
    res_y: IntProperty(name="Divisiones Y", default=32, min=1, max=512, soft_max=160, update=_on_change)

    # --- Concepto estructural ------------------------------------------------------
    support_layout: EnumProperty(
        name="Apoyos",
        items=[('NONE', "Ninguno", ""),
               ('LOW_CORNERS', "2 esquinas bajas", "Las dos esquinas más bajas"),
               ('ALL_CORNERS', "4 esquinas", ""),
               ('LOW_EDGE_MIDPOINTS', "2 centros de borde bajos", "Útil con bordes parabólicos"),
               ('CENTER', "Columna central", "")],
        default='LOW_CORNERS', update=_on_change)
    support_type: EnumProperty(
        name="Tipo de apoyo",
        items=[('PINNED', "Articulado", "Símbolo de apoyo articulado"),
               ('FIXED', "Empotrado", "Bloque de empotramiento"),
               ('COLUMN', "Columna", "Columna hasta el nivel de desplante")],
        default='PINNED', update=_on_change)
    support_size: _dist("Tamaño", 0.3, "Tamaño del símbolo o lado de la columna (conceptual)", min=0.01, soft_max=2.0)
    ground_level: _dist("Nivel de desplante", 0.0, "Cota z local del terreno", soft_min=-20, soft_max=20)
    beam_xmin: BoolProperty(name="−X", default=False, description="Viga en el borde −X", update=_on_change)
    beam_xmax: BoolProperty(name="+X", default=False, description="Viga en el borde +X", update=_on_change)
    beam_ymin: BoolProperty(name="−Y", default=False, description="Viga en el borde −Y", update=_on_change)
    beam_ymax: BoolProperty(name="+Y", default=False, description="Viga en el borde +Y", update=_on_change)
    capital_enabled: BoolProperty(name="Capiteles en los apoyos", default=False, update=_on_change,
                                  description="Engrosamiento suave bajo cada apoyo: baja el intradós, el extradós "
                                              "no cambia")
    capital_radius: _dist("Radio", 1.0, "Radio en planta del engrosamiento", min=0.05, soft_max=5.0)
    capital_extra: _dist("Espesor añadido", 0.10, "Espesor que se suma en el centro del apoyo", min=0.001,
                         soft_max=1.0, step=1)
    beam_free_edge: BoolProperty(name="Borde libre", default=False, update=_on_change,
                                 description="Viga bajo el borde libre de la bóveda (arco, cuerda o corte inclinado)")
    beam_width: _dist("Ancho", 0.2, "Ancho de las vigas de borde", min=0.01, soft_max=1.0)
    beam_depth: _dist("Peralte", 0.3, "Peralte bajo el intradós", min=0.01, soft_max=2.0)
    rib_creases: BoolProperty(name="Nervios en las limas", default=False, update=_on_change,
                              description="Nervio bajo cada lima (valle o cumbrera), centrado en ella")
    rib_width: _dist("Ancho", 0.15, "Ancho de los nervios", min=0.01, soft_max=1.0)
    rib_depth: _dist("Peralte", 0.25, "Peralte de los nervios bajo el intradós", min=0.01, soft_max=2.0)
    concrete_density: FloatProperty(name="Densidad (kg/m³)", default=2400.0, min=500.0, max=5000.0,
                                    description="Densidad del concreto reforzado", update=_on_change)

    # --- Visualización -----------------------------------------------------------------
    show_rulings: BoolProperty(name="Generatrices", default=True, update=_on_change)
    ruling_spacing: _dist("Separación", 0.5, "Separación en planta entre generatrices de una familia",
                          min=0.01, soft_max=5.0)
    ruling_layer: EnumProperty(
        name="Capa",
        items=[('EXTRADOS', "Extradós", ""), ('MID', "Media", ""), ('INTRADOS', "Intradós", "")],
        default='EXTRADOS', update=_on_change)
    ruling_radius: _dist("Grosor", 0.015, "Radio del tubo de visualización", min=0.0, soft_max=0.2, precision=4, step=1)
    show_normals: BoolProperty(name="Normales", default=False, update=_on_change)
    normal_length: _dist("Longitud", 0.5, "Longitud de los vectores normales", min=0.001, soft_max=5.0)
    normal_stride: IntProperty(name="Cada", default=4, min=1, soft_max=32, description="Mostrar una normal cada N nodos",
                               update=_on_change)
    show_supports: BoolProperty(name="Apoyos", default=True, update=_on_change)
    analysis: EnumProperty(
        name="Mapa",
        items=[('NONE', "Ninguno", ""),
               ('SLOPE', "Pendiente", "Pendiente respecto del límite de colado"),
               ('CURVATURE', "Curvatura gaussiana", "|K| (siempre K < 0 en una hypar)"),
               ('HEIGHT', "Altura", "Cota z")],
        default='NONE', update=_on_change)

    # --- Constructibilidad ----------------------------------------------------------------
    pour_slope_limit: _angle("Pendiente máx. de colado", 45.0,
                             "Criterio indicativo a partir del cual el colado suele requerir contracimbra", 5, 85)

    # --- Modularidad ---------------------------------------------------------------------------
    grid_count_x: IntProperty(name="Módulos X", default=1, min=1, max=40, soft_max=12, update=_on_change,
                              description="Número de módulos en X (1 × 1 = un solo módulo)")
    grid_count_y: IntProperty(name="Módulos Y", default=1, min=1, max=40, soft_max=12, update=_on_change,
                              description="Número de módulos en Y")
    grid_gap_x: _dist("Junta X", 0.05, "Separación entre bordes de módulos contiguos en X", min=0.0, soft_max=5.0,
                      step=1)
    grid_gap_y: _dist("Junta Y", 0.05, "Separación entre bordes de módulos contiguos en Y", min=0.0, soft_max=5.0,
                      step=1)
    grid_pattern: EnumProperty(
        name="Patrón",
        items=[('UNIFORM', "Uniforme", "Todos los módulos iguales; con inclinación forman dientes de sierra "
                "(lucernarios)"),
               ('MIRROR_X', "Espejo en columnas", "Columnas alternas reflejadas (x → −x)"),
               ('MIRROR_Y', "Espejo en filas", "Filas alternas reflejadas (y → −y)"),
               ('MIRROR_XY', "Espejo en ambas", "Columnas y filas alternas reflejadas")],
        default='UNIFORM', update=_on_change)
    grid_step_x: _dist("Escalón por columna", 0.0, "Desnivel entre columnas consecutivas de módulos",
                       soft_min=-5.0, soft_max=5.0)
    grid_step_y: _dist("Escalón por fila", 0.0, "Desnivel entre filas consecutivas de módulos",
                       soft_min=-5.0, soft_max=5.0)
    grid_instancing: EnumProperty(
        name="Representación",
        items=[('AUTO', "Automática", "Instancias cuando la retícula superaría 50 000 vértices"),
               ('INSTANCES', "Instancias", "Un módulo repetido con Geometry Nodes: ligero para retículas grandes"),
               ('REAL', "Geometría real", "Copia toda la geometría en una sola malla (más pesada)")],
        default='AUTO', update=_on_change)

    # --- Cimbra (despiece con las generatrices) ----------------------------------------------
    formwork_enabled: BoolProperty(
        name="Despiece de cimbra", default=False, update=_on_change,
        description="Tablas y vigas rectas sobre las generatrices del intradós, y puntales hasta el desplante")
    formwork_board_family: EnumProperty(
        name="Tablas según",
        items=[('A', "Familia A", "Tablas sobre las generatrices A; vigas sobre las B"),
               ('B', "Familia B", "Tablas sobre las generatrices B; vigas sobre las A")],
        default='A', update=_on_change)
    formwork_board_width: _dist("Ancho de tabla", 0.10, "Ancho de cada tabla; es también su separación en planta",
                                min=0.01, soft_max=0.5, step=1)
    formwork_board_thickness: _dist("Espesor de tabla", 0.025, "Espesor de las tablas (baja las vigas)",
                                    min=0.0, soft_max=0.1, step=1)
    formwork_joist_spacing: _dist("Separación de vigas", 0.60, "Separación en planta entre vigas (largueros)",
                                  min=0.05, soft_max=3.0)
    formwork_joist_width: _dist("Ancho de viga", 0.075, "Ancho de las vigas y lado de los puntales",
                                min=0.01, soft_max=0.5, step=1)
    formwork_joist_depth: _dist("Peralte de viga", 0.15, "Peralte de las vigas", min=0.01, soft_max=1.0, step=1)
    formwork_prop_spacing: _dist("Separación de puntales", 1.20,
                                 "Separación máxima en planta a lo largo de cada viga (0 = sin puntales)",
                                 min=0.0, soft_max=5.0)
    formwork_stock_length: _dist("Largo comercial", 4.0,
                                 "Largo de las piezas que se compran (0 = no calcular el corte)",
                                 min=0.0, soft_max=12.0)
    formwork_tolerance: _dist("Tolerancia", 0.005,
                              "Redondeo de largos para agrupar piezas en tipos y holgura admisible entre tablas",
                              min=0.0005, soft_max=0.05, precision=4, step=1)
    formwork_show: BoolProperty(name="Mostrar en 3D", default=True, update=_on_change,
                                description="Tablas, vigas y puntales como objeto hijo «Cimbra»")

    # --- Modelo de elementos finitos (no cambian la geometría: no regeneran) ---------------
    fea_element_order: EnumProperty(
        name="Elementos",
        items=[('QUADRATIC', "Cuadráticos", "S8R/S6: nodos intermedios sobre la superficie exacta (recomendado)"),
               ('LINEAR', "Lineales", "S4/S3")],
        default='QUADRATIC')
    fea_refine: IntProperty(name="Refinamiento", default=1, min=1, max=4,
                            description="Elementos por lado = divisiones de la malla × refinamiento")
    fea_young_gpa: FloatProperty(name="Módulo E (GPa)", default=25.0, min=0.1, soft_max=60.0,
                                 description="Módulo de elasticidad del concreto: dato del usuario")
    fea_poisson: FloatProperty(name="Coef. de Poisson", default=0.2, min=0.0, max=0.49)
    fea_dead_load: FloatProperty(name="Muerta adicional (kN/m²)", default=0.0, min=0.0, soft_max=10.0,
                                 description="Por m² de superficie, vertical (impermeabilización, acabados)")
    fea_live_load: FloatProperty(name="Viva (kN/m² en planta)", default=0.0, min=0.0, soft_max=10.0,
                                 description="Por m² en planta, vertical, según tu normativa")
    fea_live_half: BoolProperty(name="También en media planta", default=True,
                                description="Caso adicional con la carga viva sólo en x > 0")
    fea_pressure: FloatProperty(name="Presión normal (kN/m²)", default=0.0, soft_min=-5.0, soft_max=5.0,
                                description="Uniforme y perpendicular a la lámina; positiva hacia el intradós")
    fea_factor_dead: FloatProperty(name="γ permanentes", default=1.0, min=0.0, soft_max=2.0,
                                   description="Factor de las cargas permanentes en la combinación (tu normativa)")
    fea_factor_live: FloatProperty(name="γ variables", default=1.0, min=0.0, soft_max=2.0,
                                   description="Factor de la carga viva en la combinación (tu normativa)")
    fea_columns: EnumProperty(
        name="Columnas",
        items=[('FIXED_BASE', "Empotradas abajo", "Viga vertical hasta el desplante, empotrada en la base"),
               ('PINNED_BASE', "Articuladas abajo", "Viga vertical hasta el desplante, articulada en la base"),
               ('HEAD', "Sin columna", "Empotra los nodos de la huella en la cabeza")],
        default='FIXED_BASE')
    fea_buckling: BoolProperty(name="Pandeo lineal", default=True,
                               description="Pasos *BUCKLE con las combinaciones (ver advertencia)")
    fea_buckling_modes: IntProperty(name="Modos", default=5, min=1, max=50)
    fea_output: EnumProperty(
        name="Salida",
        items=[('3D', "Caras (3D)", "Lo predeterminado en CalculiX: resultados en las caras superior e inferior, "
                "con flexión"),
               ('2D', "Nodos (2D)", "Promedio en el espesor sobre los nodos de la lámina: sólo membrana")],
        default='3D')

    # --- Resultados FEA leídos (.frd): estado de la vista, no son parámetros ------------------
    fea_results_path: StringProperty(name="Archivo de resultados", subtype='FILE_PATH', default="")
    fea_results_key: StringProperty(default="", options={'HIDDEN'})
    fea_results_summary: StringProperty(default="", options={'HIDDEN'})
    fea_results_legend: StringProperty(default="", options={'HIDDEN'})
    fea_export_key: StringProperty(default="", options={'HIDDEN'})
    fea_result_item: EnumProperty(name="Resultado", items=_result_items, update=_on_result_change)
    fea_result_quantity: EnumProperty(
        name="Magnitud",
        items=[('DISP', "Desplazamiento |U|", "mm (en un modo de pandeo, normalizado)"),
               ('DISP_Z', "Desplazamiento vertical", "Uz en mm, positivo hacia arriba"),
               ('VON_MISES', "Von Mises", "MPa"),
               ('S_MAX', "Principal máxima", "MPa; positiva = tracción"),
               ('S_MIN', "Principal mínima", "MPa; negativa = compresión"),
               ('N_MAX', "N₁ membrana máx.", "kN/m, fuerza de membrana principal máxima (tracción +)"),
               ('N_MIN', "N₂ membrana mín.", "kN/m, fuerza de membrana principal mínima (compresión −)"),
               ('N_X', "Nx", "kN/m, ejes locales: x = proyección del X global"),
               ('N_Y', "Ny", "kN/m"),
               ('N_XY', "Nxy", "kN/m, cortante de membrana"),
               ('M_MAX', "M₁ momento máx.", "kN·m/m, momento principal máximo (M > 0 tracciona el extradós)"),
               ('M_MIN', "M₂ momento mín.", "kN·m/m, momento principal mínimo"),
               ('M_X', "Mx", "kN·m/m, flexión alrededor de y local"),
               ('M_Y', "My", "kN·m/m"),
               ('M_XY', "Mxy", "kN·m/m, momento torsor")],
        default='DISP', update=_on_result_change)
    fea_result_show: BoolProperty(name="Colorear el cascarón", default=True, update=_on_result_change)
    fea_result_deformed: BoolProperty(name="Deformada", default=False, update=_on_result_change,
                                      description="Copia del cascarón desplazada (objeto hijo «Deformada»)")
    fea_result_scale: FloatProperty(name="Escala", default=0.0, min=0.0, soft_max=10000.0, update=_on_result_change,
                                    description="Factor de la deformada; 0 = automático (el máximo se dibuja "
                                                "como el 5 % del tamaño)")

    # --- Estado interno ----------------------------------------------------------------------
    report_json: StringProperty(default="", options={'HIDDEN'})
    fea_summary_json: StringProperty(default="", options={'HIDDEN'})
    variants: CollectionProperty(type=CANDELA_PG_variant)
    variant_index: IntProperty(default=0)


classes = (CANDELA_PG_variant, CANDELA_PG_shell)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.candela = PointerProperty(type=CANDELA_PG_shell)


def unregister():
    del bpy.types.Object.candela
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

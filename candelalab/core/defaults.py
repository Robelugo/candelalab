"""Parámetros comunes a todas las familias de cascarón (mismas claves que en Blender)."""

import math

COMMON_DEFAULTS = {
    # espesor y malla
    "thickness": 0.04,
    "thickness_mode": "NORMAL",
    "thickness_reference": "INTRADOS",
    "output": "SOLID",
    "res_x": 32,
    "res_y": 32,
    # concepto estructural
    "support_type": "PINNED",
    "support_size": 0.3,
    "ground_level": 0.0,
    "beam_xmin": False, "beam_xmax": False, "beam_ymin": False, "beam_ymax": False,
    "beam_width": 0.2,
    "beam_depth": 0.3,
    "beam_free_edge": False,  # viga bajo el borde libre de una bóveda radial
    # capiteles: engrosamiento bajo los apoyos
    "capital_enabled": False,
    "capital_radius": 1.0,
    "capital_extra": 0.10,
    # rigidizadores
    "rib_creases": False,
    "rib_width": 0.15,
    "rib_depth": 0.25,
    "concrete_density": 2400.0,
    # visualización
    "show_rulings": True,
    "ruling_spacing": 0.5,
    "ruling_layer": "EXTRADOS",
    "ruling_radius": 0.015,
    "show_normals": False,
    "normal_length": 0.5,
    "normal_stride": 4,
    "show_supports": True,
    "analysis": "NONE",
    # constructibilidad
    "pour_slope_limit": math.radians(45.0),
    # modularidad (1 × 1 = un solo módulo)
    "grid_count_x": 1,
    "grid_count_y": 1,
    "grid_gap_x": 0.05,
    "grid_gap_y": 0.05,
    "grid_pattern": "UNIFORM",
    "grid_step_x": 0.0,
    "grid_step_y": 0.0,
    "grid_instancing": "AUTO",  # AUTO / INSTANCES / REAL
    # despiece de cimbra (generatrices del intradós)
    "formwork_enabled": False,
    "formwork_board_family": "A",     # familia de las tablas; las vigas siguen la otra
    "formwork_board_width": 0.10,
    "formwork_board_thickness": 0.025,
    "formwork_joist_spacing": 0.60,
    "formwork_joist_width": 0.075,
    "formwork_joist_depth": 0.15,
    "formwork_prop_spacing": 1.20,    # 0 = sin puntales
    "formwork_stock_length": 4.0,     # 0 = sin corte de piezas comerciales
    "formwork_tolerance": 0.005,
    "formwork_show": True,
    # modelo de elementos finitos (exportación; todos los valores son datos del usuario)
    "fea_element_order": "QUADRATIC",  # QUADRATIC (S8R/S6) / LINEAR (S4/S3)
    "fea_refine": 1,                   # elementos = divisiones × refinamiento
    "fea_young_gpa": 25.0,
    "fea_poisson": 0.2,
    "fea_dead_load": 0.0,              # kN/m² de superficie
    "fea_live_load": 0.0,              # kN/m² en planta
    "fea_live_half": True,
    "fea_pressure": 0.0,               # kN/m², positiva hacia el intradós
    "fea_factor_dead": 1.0,
    "fea_factor_live": 1.0,
    "fea_columns": "FIXED_BASE",       # FIXED_BASE / PINNED_BASE / HEAD
    "fea_buckling": True,
    "fea_buckling_modes": 5,
    "fea_output": "3D",                # 3D: caras de la lámina (con flexión) / 2D: nodos originales
}

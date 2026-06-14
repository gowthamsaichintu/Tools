# Defines active OMS pipelines for validation.
# Each pipeline maps layer name → interface class name.
# Order matters: base → interpreter → functional (short-circuit flows top-down).
#
# Interfaces available:
#   • CBaseCrs, CIntprCrs, CFctCrs (CRS pipeline: child asset detection)
#   • CBaseBp3d, CIntprOpos, CFctOpos (OPOS pipeline: occupancy & pose)
#   • CBaseEsd, CBaseBp3dUntracked (base layers; no interpreter/functional)
#   • CIntprSocc (seat mapping: identifies which persons are in which seats)

# Cross-pipeline dependencies:
#   • CIntprSocc (SeatMapping interpreter) requires inputs from:
#     - CBaseBp3d (BodyPose base: person detection)
#     - CBaseCrs (CRS base: child asset detection)
#     → Maps persons to their seat positions
#
PIPELINES = {
    "CRS": {
        "base":        "CBaseCrs",
        "interpreter": "CIntprCrs",
        "functional":  "CFctCrs",
    },
    "OPOS": {
        "base":        "CBaseBp3d",
        "interpreter": "CIntprOpos",
        "functional":  "CFctOpos",
    },
    "SeatMapping": {
        "base":        "CBaseBp3d",
        "interpreter": "CIntprSocc",    # maps persons to seats (uses CBaseBp3d + CBaseCrs inputs)
        "functional":  "CFctOpos",
    },
}

# Short-circuit rules:
# If a layer fails, all layers below it are SKIPPED.
LAYER_ORDER = ["base", "interpreter", "functional"]

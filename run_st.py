import os
import sys
import glob
import ctypes
import yaml
import re
import numpy as np

from pipeline_registry import PIPELINES, LAYER_ORDER
from validator import validate_layer, LayerResult
from reporter import TestResult, print_report, generate_html_report

# ── env ────────────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
PY_PATH  = os.path.join(ROOT_DIR, "conan/python")
LIB_PATH = os.path.join(ROOT_DIR, "conan/lib")
ROOT_LIB_PATH = os.path.join(ROOT_DIR, "lib")
LOCAL_PYTHON_PATH = os.path.join(ROOT_DIR, "python")

sys.path.insert(0, PY_PATH)
sys.path.insert(0, LOCAL_PYTHON_PATH)
ld_library_paths = [ROOT_LIB_PATH, LIB_PATH, os.environ.get('LD_LIBRARY_PATH', '')]
os.environ["LD_LIBRARY_PATH"] = ":".join([p for p in ld_library_paths if p])

CATALOG_DIR = os.path.join(ROOT_DIR, "catalogs")
TESTS_DIR   = os.path.join(ROOT_DIR, "tests")


# ── plugin loader ──────────────────────────────────────────────────────────────
def load_plugins():
    plugin_paths = []
    plugin_paths.extend(glob.glob(os.path.join(LIB_PATH, "libbytesoup_reader_*_plugin.so")))
    plugin_paths.extend(glob.glob(os.path.join(ROOT_LIB_PATH, "libbytesoup_reader_*_plugin.so")))

    for so in sorted(set(plugin_paths)):
        try:
            ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
        except Exception as e:
            print(f"WARNING: plugin load failed {so}: {e}")


# ── discover: list all interfaces ─────────────────────────────────────────────
def discover_interfaces(bs_file: str):
    """Print all unique class names found in the bag."""
    from py_aosbag import AOSBag, View, Query
    from py_data_accessor import PyDataAccessorFactory

    bag = AOSBag()
    if not bag.open(bs_file):
        print("ERROR: Cannot open bytesoup")
        sys.exit(1)

    query   = Query([".*"])
    view    = View(bag, query)
    factory = PyDataAccessorFactory()
    factory.register_as_dict(bag.getClassInfoHashModels())

    seen = set()
    for sample in view:
        accessor = factory.get_py_data_accessor(sample.classHash())
        if not accessor:
            continue
        obj        = accessor.from_buffer(sample.getData())[0]
        class_name = obj._meta_data.class_name
        if class_name not in seen:
            print(f"  FOUND: {class_name}")
            seen.add(class_name)

    print(f"\nTotal interfaces: {len(seen)}")


# ── discover: generate catalog for one interface ───────────────────────────────
def discover_catalog(bs_file: str, interface: str, output_yaml: str):
    """Generate a catalog yaml for a specific interface."""
    from py_aosbag import AOSBag, View, Query
    from py_data_accessor import PyDataAccessorFactory

    bag = AOSBag()
    if not bag.open(bs_file):
        print("ERROR: Cannot open bytesoup")
        sys.exit(1)

    query   = Query([".*"])
    view    = View(bag, query)
    factory = PyDataAccessorFactory()
    factory.register_as_dict(bag.getClassInfoHashModels())

    for sample in view:
        accessor = factory.get_py_data_accessor(sample.classHash())
        if not accessor:
            continue

        obj        = accessor.from_buffer(sample.getData())[0]
        class_name = obj._meta_data.class_name

        if not class_name.endswith(interface):
            continue

        print(f"Found: {class_name}")
        d     = obj.to_dict()
        paths = _discover_dict(d)
        print(f"Signals found: {len(paths)}")

        _generate_catalog(paths, output_yaml)
        return

    print(f"ERROR: Interface '{interface}' not found in bag")
    sys.exit(1)


def _discover_dict(obj, prefix="", results=None):
    if results is None:
        results = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            new_prefix = f"{prefix}.{k}" if prefix else k
            _discover_dict(v, new_prefix, results)

    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _discover_dict(v, f"{prefix}[{i}]", results)

    else:
        t = obj.dtype.name if isinstance(obj, np.generic) else type(obj).__name__
        results.append((prefix, t))

    return results


def _generate_catalog(paths: list, output_yaml: str):
    catalog    = {"signals": []}
    used_names = {}

    os.makedirs(os.path.dirname(os.path.abspath(output_yaml)), exist_ok=True)

    for path, typ in paths:
        if not path:
            continue

        name = path.split(".")[-1]
        name = name.replace("[", "_").replace("]", "")

        if name in used_names:
            used_names[name] += 1
            name = f"{name}_{used_names[name]}"
        else:
            used_names[name] = 0

        t = typ if typ in ("int", "float", "bool") else "string"

        catalog["signals"].append({
            "name": name,
            "path": path,
            "type": t,
        })

    with open(output_yaml, "w") as f:
        yaml.dump(catalog, f, sort_keys=False, default_flow_style=False)

    print(f"Catalog written: {output_yaml}")


# ── bag reader ─────────────────────────────────────────────────────────────────
def collect_objects(bs_file: str, interfaces: set) -> dict:
    """
    Reads the bag once, collects all deserialized objects per interface class.
    Uses endswith matching to handle full namespace prefixes.
    Returns {short_class_name: [obj, ...]}
    """
    from py_aosbag import AOSBag, View, Query
    from py_data_accessor import PyDataAccessorFactory

    bag = AOSBag()
    if not bag.open(bs_file):
        print("ERROR: Cannot open bytesoup")
        sys.exit(1)

    query   = Query([".*"])
    view    = View(bag, query)
    factory = PyDataAccessorFactory()
    factory.register_as_dict(bag.getClassInfoHashModels())

    collected: dict = {ifc: [] for ifc in interfaces}

    for sample in view:
        accessor = factory.get_py_data_accessor(sample.classHash())
        if not accessor:
            continue

        obj        = accessor.from_buffer(sample.getData())[0]
        class_name = obj._meta_data.class_name

        for key in collected:
            if class_name.endswith(key):
                collected[key].append(obj)
                break

    return collected


# ── test loader ────────────────────────────────────────────────────────────────
def load_tests(tests_dir: str) -> list:
    tests = []
    for path in sorted(glob.glob(os.path.join(tests_dir, "*.yaml"))):
        with open(path) as f:
            t = yaml.safe_load(f)
            t["_file"] = path
            tests.append(t)
    return tests


# ── per-test runner ────────────────────────────────────────────────────────────
def run_test(test: dict, objects: dict) -> TestResult:
    result = TestResult(
        name=test.get("testcase", "unnamed"),
        pipeline=test.get("pipeline", "unknown"),
    )

    pipeline_def = PIPELINES.get(result.pipeline)
    if pipeline_def is None:
        print(f"  ERROR: Unknown pipeline '{result.pipeline}'")
        return result

    # Group checks by layer
    checks_by_layer: dict = {l: [] for l in LAYER_ORDER}
    for v in test.get("validations", []):
        layer = v.get("layer")
        if layer in checks_by_layer:
            checks_by_layer[layer].append(v)

    # Walk layers in order — short-circuit on FAIL
    short_circuit = False
    base_frame = None

    def _get_frame_number(obj):
        try:
            d = obj.to_dict()
            md = d.get('m_metadata', {})
            fn = md.get('m_frameNumber')
            if fn is None:
                return None
            return int(fn)
        except Exception:
            return None

    for layer in LAYER_ORDER:
        interface = pipeline_def.get(layer)

        if interface is None:
            continue

        if short_circuit:
            lr        = LayerResult(layer)
            lr.status = "SKIPPED"
            result.layers[layer] = lr
            continue

        checks = checks_by_layer.get(layer, [])

        if not checks:
            lr        = LayerResult(layer)
            lr.status = "SKIPPED"
            result.layers[layer] = lr
            continue

        obj_list = objects.get(interface, [])
        lr = None
        selected_obj = None

        if not obj_list:
            lr = validate_layer(layer, interface, checks, None, CATALOG_DIR)
        else:
            # If we have a base frame, prefer a candidate that matches that frame
            if base_frame is not None:
                for candidate in obj_list:
                    if _get_frame_number(candidate) == base_frame:
                        lr_candidate = validate_layer(layer, interface, checks, candidate, CATALOG_DIR)
                        selected_obj = candidate
                        lr = lr_candidate
                        break

            # Otherwise, or if matching-frame candidate didn't provide data, try any candidate
            if lr is None:
                for candidate in obj_list:
                    lr_candidate = validate_layer(layer, interface, checks, candidate, CATALOG_DIR)
                    if lr_candidate.status != "NO_DATA":
                        lr = lr_candidate
                        selected_obj = candidate
                        break

            # If none provided data, fall back to the first candidate's result
            if lr is None:
                selected_obj = obj_list[0]
                lr = validate_layer(layer, interface, checks, selected_obj, CATALOG_DIR)

        result.layers[layer] = lr

        # Record base frame number to align subsequent layers
        if layer == "base" and selected_obj is not None:
            bf = _get_frame_number(selected_obj)
            if bf is not None:
                base_frame = bf

        if lr.failed():
            short_circuit = True

    return result


# ── entry point ────────────────────────────────────────────────────────────────
def print_usage():
    print(
        "\nUsage:\n"
        "  python3 run_st.py <bs_file> <tests_dir> [--html <output.html>]\n"
        "  python3 run_st.py <bs_file> --list-interfaces\n"
        "  python3 run_st.py <bs_file> --discover <InterfaceName> <output.yaml>\n"
        "\nExamples:\n"
        "  python3 run_st.py recording.bs tests/\n"
        "  python3 run_st.py recording.bs tests/ --html report.html\n"
        "  python3 run_st.py recording.bs --list-interfaces\n"
        "  python3 run_st.py recording.bs --discover CBaseCrs catalogs/CBaseCrs.yaml\n"
    )


def main():
    if len(sys.argv) < 3:
        print_usage()
        sys.exit(1)

    bs_file = sys.argv[1]
    load_plugins()

    # ── list all interfaces in bag ─────────────────────────────────────────────
    if sys.argv[2] == "--list-interfaces":
        discover_interfaces(bs_file)
        return

    # ── generate catalog for one interface ────────────────────────────────────
    if sys.argv[2] == "--discover":
        if len(sys.argv) < 5:
            print("ERROR: --discover requires <InterfaceName> and <output.yaml>")
            print_usage()
            sys.exit(1)
        discover_catalog(bs_file, sys.argv[3], sys.argv[4])
        return

    # ── normal smoke test run ─────────────────────────────────────────────────
    tests_dir = sys.argv[2]

    tests = load_tests(tests_dir)
    if not tests:
        print(f"ERROR: No test YAMLs found in {tests_dir}")
        sys.exit(1)

    print(f"Loaded {len(tests)} test(s) from {tests_dir}")

    # Collect all interfaces needed across all tests
    needed_interfaces: set = set()
    for t in tests:
        pd = PIPELINES.get(t.get("pipeline", ""))
        if pd:
            needed_interfaces.update(pd.values())

    print(f"Reading bag: {bs_file}")
    objects = collect_objects(bs_file, needed_interfaces)

    for ifc, objs in objects.items():
        print(f"  {ifc}: {len(objs)} frame(s)")

    results = [run_test(t, objects) for t in tests]

    # Print terminal report
    all_pass = print_report(results)

    # Check if user requested HTML output
    html_file = None
    if len(sys.argv) > 3 and sys.argv[3] == "--html":
        if len(sys.argv) > 4:
            html_file = sys.argv[4]
        else:
            print("ERROR: --html requires output filename")
            sys.exit(1)

    if html_file:
        generate_html_report(results, html_file)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()

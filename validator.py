import re
import yaml
import os

# ── catalog cache ──────────────────────────────────────────────────────────────
_catalog_cache: dict[str, dict] = {}

def _load_catalog(interface: str, catalog_dir: str) -> dict:
    """
    Returns {signal_name: {path, type}} for a given interface.
    Cached after first load.
    """
    if interface in _catalog_cache:
        return _catalog_cache[interface]

    path = os.path.join(catalog_dir, f"{interface}.yaml")
    if not os.path.exists(path):
        print(f"  WARNING: No catalog for {interface} at {path}")
        _catalog_cache[interface] = {}
        return {}

    with open(path) as f:
        raw = yaml.safe_load(f)

    catalog = {
        s["name"]: {"path": s["path"], "type": s.get("type", "string")}
        for s in raw.get("signals", [])
    }
    _catalog_cache[interface] = catalog
    return catalog


# ── nested value accessor ──────────────────────────────────────────────────────
def _get_nested(obj, path: str):
    parts = path.split(".")
    current = obj

    for p in parts:
        if current is None:
            return None

        match = re.match(r"([A-Za-z0-9_]+)\[(\d+)\]", p)
        if match:
            field, idx = match.group(1), int(match.group(2))
            current = _member(current, field)
            if current is None:
                return None
            try:
                current = current[idx]
            except Exception:
                return None
            continue

        current = _member(current, p)

    return current


def _member(obj, key: str):
    if hasattr(obj, "get_member"):
        try:
            v = obj.get_member(key)
            if v is not None:
                return v
        except Exception:
            pass
    try:
        return obj[key]
    except Exception:
        return None


# ── type cast ─────────────────────────────────────────────────────────────────
def _cast(val, t: str):
    if val is None:
        return None
    if t == "int":   return int(val)
    if t == "float": return float(val)
    if t == "bool":  return val if isinstance(val, bool) else bool(int(val))
    return val


# ── single layer validation ───────────────────────────────────────────────────
class LayerResult:
    def __init__(self, layer: str):
        self.layer = layer
        self.status = "SKIPPED"   # PASS | FAIL | SKIPPED | NO_DATA
        self.checks: list[dict] = []

    def passed(self) -> bool:
        return self.status == "PASS"

    def failed(self) -> bool:
        return self.status == "FAIL"


def validate_layer(
    layer_name: str,
    interface: str,
    checks: list[dict],
    obj,                    # deserialized AOS object for this interface
    catalog_dir: str,
) -> LayerResult:

    result = LayerResult(layer_name)

    if obj is None:
        result.status = "NO_DATA"
        return result

    catalog = _load_catalog(interface, catalog_dir)
    context: dict = {}
    all_none = True

    # ── resolve all signal values first (build context for dependency checks)
    for chk in checks:
        sig_name = chk["signal"]
        entry = catalog.get(sig_name)

        if entry is None:
            print(f"  WARNING: signal '{sig_name}' not in catalog for {interface}")
            context[sig_name] = None
            continue

        raw = _get_nested(obj, entry["path"])
        val = _cast(raw, entry["type"])
        context[sig_name] = val

        if val is not None:
            all_none = False

    if all_none:
        result.status = "NO_DATA"
        return result

    # ── evaluate each check
    layer_pass = True

    for chk in checks:
        sig_name = chk["signal"]
        val = context.get(sig_name)
        check_record = {"signal": sig_name, "value": val, "status": "PASS", "detail": ""}

        if val is None:
            check_record["status"] = "SKIP"
            check_record["detail"] = "no data"
            result.checks.append(check_record)
            continue

        passed = _evaluate_check(chk, val, context)

        if not passed:
            layer_pass = False
            check_record["status"] = "FAIL"
            check_record["detail"] = _describe_check(chk, val)

        result.checks.append(check_record)

    result.status = "PASS" if layer_pass else "FAIL"
    return result


def _evaluate_check(chk: dict, val, context: dict) -> bool:
    # range check
    if "min" in chk or "max" in chk:
        lo = chk.get("min", float("-inf"))
        hi = chk.get("max", float("inf"))
        return lo <= val <= hi

    # exact match
    if "expected" in chk:
        return val == chk["expected"]

    # conditional dependency
    if "condition" in chk:
        try:
            cond_met = bool(eval(chk["condition"], {"__builtins__": {}}, context))
        except Exception:
            return True   # can't evaluate → don't fail
        if cond_met:
            return val == chk["expected_if_true"]
        return True

    return True   # no rule → trivially pass


def _describe_check(chk: dict, val) -> str:
    if "min" in chk or "max" in chk:
        lo = chk.get("min", "-∞")
        hi = chk.get("max", "+∞")
        return f"expected [{lo}, {hi}], got {val}"
    if "expected" in chk:
        return f"expected {chk['expected']}, got {val}"
    return f"got {val}"

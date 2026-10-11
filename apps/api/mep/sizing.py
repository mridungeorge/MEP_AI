"""Equal-friction duct sizing in SI (the OpenMEP approach: size a round duct for the friction rate, convert to a rectangle of equal capacity, round up to a size
increment), plus the airflow-balance check.

This is ARITHMETIC on figures the designer and the firm choose; it encodes no standard. Nothing here decides compliance: results are SIZED / NO DATA and
velocity notes read WITHIN LIMIT / ABOVE LIMIT / NO LIMIT SET against firm settings (which have no default: a limit is the firm's choice, never invented here).
Physical constants (air density and viscosity at about 20 C) are fixed and reported in every result.

Method
* Round duct: Darcy-Weisbach, dp/L = f * rho * v^2 / (2 D), f from Colebrook-White (fixed-point), D found by bisection for the target friction rate.
* Rectangle of equal capacity (equal friction, equal flow): Huebscher, De = 1.30 (a b)^0.625 / (a + b)^0.25 (a published fit; accurate for aspect ratios up to about 8).
* Sizes round UP to the firm's increment, so friction only falls; actual velocity is flow / area.
"""
import math
from dataclasses import asdict, dataclass
from typing import Any

RHO = 1.2          # kg/m3, air at about 20 C
MU = 1.81e-5       # Pa s
SETTING_LIMITS = {"friction_pa_m": (0.05, 20.0), "roughness_mm": (0.0, 5.0), "size_increment_mm": (5.0, 200.0), "target_aspect": (1.0, 8.0),
                  "max_aspect": (1.0, 8.0), "min_size_mm": (50.0, 500.0), "max_velocity_main_ms": (0.5, 40.0), "max_velocity_branch_ms": (0.5, 40.0),
                  "max_depth_mm": (50.0, 5000.0), "balance_tolerance_pct": (0.0, 20.0)}
DEFAULTS: dict[str, float | None] = {"friction_pa_m": 1.0, "roughness_mm": 0.09, "size_increment_mm": 50.0, "target_aspect": 2.0, "max_aspect": 4.0, "min_size_mm": 150.0,
                                     "max_velocity_main_ms": None, "max_velocity_branch_ms": None, "max_depth_mm": None, "balance_tolerance_pct": 1.0}


def clean_settings(raw: dict[str, Any] | None) -> dict[str, float | None]:
    """Defaults overlaid with the firm's values; unknown keys, non-numbers and out-of-range values raise ValueError."""
    out = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k not in SETTING_LIMITS:
            raise ValueError(f"unknown sizing setting {k!r}")
        if v is None:
            out[k] = None if DEFAULTS[k] is None else DEFAULTS[k]
            continue
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise ValueError(f"{k} must be a finite number")
        lo, hi = SETTING_LIMITS[k]
        if not lo <= v <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
        out[k] = float(v)
    return out


def friction_factor(re: float, rel_rough: float) -> float:
    f = 0.02
    for _ in range(60):
        new = (-2.0 * math.log10(rel_rough / 3.7 + 2.51 / (re * math.sqrt(f)))) ** -2
        if abs(new - f) < 1e-12:
            return new
        f = new
    return f


def friction_pa_per_m(flow_m3s: float, d_m: float, rough_m: float) -> float:
    v = flow_m3s / (math.pi * d_m * d_m / 4.0)
    re = RHO * v * d_m / MU
    f = 64.0 / re if re < 2300 else friction_factor(re, rough_m / d_m)
    return f * RHO * v * v / (2.0 * d_m)


def round_diameter(flow_m3s: float, friction: float, rough_m: float) -> float:
    """Diameter (m) whose friction at `flow_m3s` equals `friction` Pa/m (friction falls as D grows)."""
    lo, hi = 0.01, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if friction_pa_per_m(flow_m3s, mid, rough_m) > friction:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def equivalent_diameter(a_mm: float, b_mm: float) -> float:
    return 1.30 * (a_mm * b_mm) ** 0.625 / (a_mm + b_mm) ** 0.25


def _up(x: float, step: float) -> float:
    return math.ceil(x / step - 1e-9) * step


@dataclass(frozen=True)
class Sized:
    airflow_ls: float
    friction_target_pa_m: float
    equivalent_diameter_mm: float
    shape: str
    width_mm: float | None
    depth_mm: float | None
    diameter_mm: float | None
    velocity_ms: float
    friction_actual_pa_m: float
    aspect: float | None
    notes: list[str]
    unsound: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def size_duct(airflow_ls: float, shape: str, s: dict[str, float | None], depth_cap_mm: float | None = None) -> Sized:
    """Size one duct. `s` comes from clean_settings. `depth_cap_mm` (for example the ceiling void less insulation and clearance) limits a rectangle's depth."""
    if not math.isfinite(airflow_ls) or airflow_ls <= 0:
        raise ValueError("airflow must be a positive finite number")
    q = airflow_ls / 1000.0
    fr, rough_m, step = float(s["friction_pa_m"] or 0), float(s["roughness_mm"] or 0) / 1000.0, float(s["size_increment_mm"] or 50)
    min_size = float(s["min_size_mm"] or 0)
    d_mm = round_diameter(q, fr, rough_m) * 1000.0
    notes: list[str] = []
    if shape == "round":
        dia = max(_up(d_mm, step), min_size)
        area = math.pi * (dia / 1000.0) ** 2 / 4.0
        actual = friction_pa_per_m(q, dia / 1000.0, rough_m)
        return _sound(Sized(airflow_ls, fr, round(d_mm, 1), "round", None, None, dia, round(q / area, 3), round(actual, 4), None, notes), d_mm)
    aspect = float(s["target_aspect"] or 2.0)
    depth = (d_mm * (aspect + 1) ** 0.25) / (1.30 * aspect ** 0.625)
    depth = max(_up(depth, step), min_size)
    cap = depth_cap_mm if depth_cap_mm is not None else s["max_depth_mm"]
    if cap is not None and depth > cap:
        depth = max(math.floor(cap / step) * step, step)
        notes.append(f"depth held to {depth:g} mm by the depth limit")
    width = depth
    while equivalent_diameter(width, depth) < d_mm and width < 20000:
        width += step
    width = max(_up(width, step), min_size)
    if width < depth:
        width, depth = depth, width
    ratio = width / depth
    if ratio > float(s["max_aspect"] or 8.0):
        notes.append(f"aspect ratio {ratio:.1f} is above the firm's {s['max_aspect']:g}")
    de = equivalent_diameter(width, depth)
    return _sound(Sized(airflow_ls, fr, round(d_mm, 1), "rect", width, depth, None, round(q / (width * depth / 1e6), 3),
                        round(friction_pa_per_m(q, de / 1000.0, rough_m), 4), round(ratio, 2), notes), d_mm, cap=cap)


def _sound(sized: Sized, d_mm: float, cap: float | None = None) -> Sized:
    """Mark a result UNSOUND when the arithmetic could not honour the settings: friction above the target, beyond the sizing range, or a depth above its limit."""
    notes = list(sized.notes)
    if d_mm >= 4990.0:
        notes.append("beyond the range this sizing covers (5 m equivalent diameter)")
    if sized.friction_actual_pa_m > sized.friction_target_pa_m * 1.001:
        notes.append(f"could not meet the friction rate: {sized.friction_actual_pa_m:g} Pa/m against {sized.friction_target_pa_m:g}")
    if cap is not None and sized.depth_mm is not None and sized.depth_mm > cap + 1e-9:
        notes.append(f"the depth limit ({cap:g} mm) is smaller than the size step")
    if sized.width_mm is not None and sized.width_mm >= 20000:
        notes.append("width reached the 20 m search limit")
    return Sized(**{**sized.as_dict(), "notes": notes, "unsound": len(notes) > len(sized.notes)})


def velocity_note(velocity: float, role: str, s: dict[str, float | None]) -> str:
    limit = s["max_velocity_main_ms"] if role == "main" else s["max_velocity_branch_ms"]
    if limit is None:
        return "NO LIMIT SET"
    return "WITHIN LIMIT" if velocity <= limit else "ABOVE LIMIT"


def balance(runs: list[dict[str, Any]], tolerance_pct: float) -> list[dict[str, Any]]:
    """Per system tag: the largest duct airflow (the trunk) against the sum of terminal airflows. Both must exist, otherwise NO DATA."""
    systems: dict[str, dict[str, Any]] = {}
    for r in runs:
        tag = r.get("system_tag")
        if not tag or r.get("airflow_ls") is None:
            continue
        sysrow = systems.setdefault(tag, {"trunk": 0.0, "terminals": 0.0, "n_terminals": 0, "n_ducts": 0})
        if r["kind"] == "duct":
            sysrow["trunk"] = max(sysrow["trunk"], float(r["airflow_ls"]))
            sysrow["n_ducts"] += 1
        elif r["kind"] == "terminal":
            sysrow["terminals"] += float(r["airflow_ls"]) * int(r.get("quantity") or 1)
            sysrow["n_terminals"] += 1
    out = []
    for tag, v in sorted(systems.items()):
        if v["n_ducts"] == 0 or v["n_terminals"] == 0:
            out.append({"system_tag": tag, "trunk_ls": v["trunk"], "terminals_ls": v["terminals"], "difference_pct": None, "status": "NO DATA"})
            continue
        diff = abs(v["trunk"] - v["terminals"]) / max(v["trunk"], v["terminals"]) * 100.0 if max(v["trunk"], v["terminals"]) > 0 else 0.0
        out.append({"system_tag": tag, "trunk_ls": round(v["trunk"], 3), "terminals_ls": round(v["terminals"], 3), "difference_pct": round(diff, 2),
                    "status": "BALANCED" if diff <= tolerance_pct else "UNBALANCED"})
    return out

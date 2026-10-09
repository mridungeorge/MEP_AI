"""Plain-language shortcut: a sentence typed by the designer, read into the spec card by fixed patterns (no model, no guessing).

    "600x400 to 300 dia, 500 long, 0.8 mm sheet, pittsburgh seam 25, tdc 30, mark TR-01"

The result is a PROPOSAL shown back to the designer field by field (`understood`), with everything the parser had to assume (`assumptions`)
and every required field it could not find (`missing`, each with a plain-language question). Nothing is built until the designer confirms the
filled-in card. A value the sentence did not state is never invented: it is asked for.
"""
import re
from dataclasses import dataclass, field
from typing import Any

from mep.skills_runner.form import build_form
from mep.skills_runner.registry import get_skill

NUM = r"(\d+(?:\.\d+)?)"
X = r"\s*[x×*]\s*"


@dataclass
class Parsed:
    spec: dict[str, Any] = field(default_factory=dict)
    understood: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)

    def put(self, path: str, value: Any, text: str) -> None:
        node = self.spec
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        self.understood.append({"text": text.strip(), "field": path, "value": value})


def _f(x: str) -> float:
    v = float(x)
    return int(v) if v.is_integer() else v


# ------------------------------------------------------------------------------------------------------ duct-fab
def _duct_fab(text: str, p: Parsed) -> None:
    t = text.replace("Ø", "ø")
    low = t.lower()
    m = re.search(NUM + X + NUM, low)
    inlet = (m.group(1), m.group(2)) if m else None
    rest = low[m.end():] if m else low
    to_rect = re.search(r"\bto\s+" + NUM + X + NUM, rest)
    to_round = re.search(r"\bto\s+(?:ø\s*)?" + NUM + r"\s*(?:mm)?\s*(?:dia\w*|round|diameter|ø)", rest) or re.search(r"\bto\s+ø\s*" + NUM, rest)
    offset = re.search(r"\boffset(?:\s+of)?\s+(-?" + NUM + r")\s*(?:,|x|×|/|and)?\s*(-?" + NUM + ")?", rest)
    fitting = None
    if inlet and to_rect:
        fitting = "rect_reducer"
        p.put("geometry.width_in_mm", _f(inlet[0]), m.group(0))                     # type: ignore[union-attr]
        p.put("geometry.height_in_mm", _f(inlet[1]), m.group(0))                    # type: ignore[union-attr]
        p.put("geometry.width_out_mm", _f(to_rect.group(1)), to_rect.group(0))
        p.put("geometry.height_out_mm", _f(to_rect.group(2)), to_rect.group(0))
    elif inlet and to_round:
        fitting = "rect_to_round"
        p.put("geometry.width_mm", _f(inlet[0]), m.group(0))                        # type: ignore[union-attr]
        p.put("geometry.height_mm", _f(inlet[1]), m.group(0))                       # type: ignore[union-attr]
        p.put("geometry.diameter_mm", _f(to_round.group(1)), to_round.group(0))
    elif inlet and offset:
        fitting = "rect_offset"
        p.put("geometry.width_mm", _f(inlet[0]), m.group(0))                        # type: ignore[union-attr]
        p.put("geometry.height_mm", _f(inlet[1]), m.group(0))                       # type: ignore[union-attr]
    if offset and fitting in ("rect_offset", "rect_to_round"):
        ox, oy = offset.group(1), offset.group(4) if offset.lastindex and offset.lastindex >= 4 else None
        p.put("geometry.offset_x_mm", _f(ox), offset.group(0))
        if oy is not None:
            p.put("geometry.offset_y_mm", _f(oy), offset.group(0))
        else:
            p.assumptions.append("only one offset was given: read as the X offset (Y offset left for you to set)")
    if fitting:
        p.put("fitting", fitting, "the shape of the sentence")
    am = re.search(r"(concentric|flat[ _-](?:bottom|top|left|right))", low)
    if am:
        p.put("geometry.alignment", am.group(1).replace(" ", "_").replace("-", "_"), am.group(0))
    lm = re.search(NUM + r"\s*(?:mm)?\s*(?:long|length|lg)\b", low) or re.search(r"\b(?:length|l)\s*[=:]?\s*" + NUM, low)
    if lm:
        p.put("geometry.length_mm", _f(lm.group(1)), lm.group(0))
    tm = re.search(NUM + r"\s*mm\s*(?:sheet|thick\w*|steel|galv\w*|gauge)", low) or re.search(r"(?:sheet|thickness)\s*[=:]?\s*" + NUM, low)
    if tm:
        p.put("sheet_thickness_mm", _f(tm.group(1)), tm.group(0))
    sm = re.search(r"(pittsburgh|snap[ _-]?lock|grooved|welded)(?:\s+seam)?\s*(?:allowance\s*)?" + NUM + r"?", low)
    if sm:
        p.put("seam.type", sm.group(1).replace(" ", "_").replace("-", "_"), sm.group(0))
        if sm.group(2):
            p.put("seam.allowance_mm", _f(sm.group(2)), sm.group(0))
    else:
        sa = re.search(r"seam\s*(?:allowance)?\s*" + NUM, low)
        if sa:
            p.put("seam.allowance_mm", _f(sa.group(1)), sa.group(0))
    cm = re.search(r"(tdc|slip[ _-]?and[ _-]?drive|angle[ _-]?flange|plain)(?:\s+conn\w*)?\s*(?:allowance\s*)?" + NUM + r"?", low)
    if cm:
        p.put("connection.type", cm.group(1).replace(" ", "_").replace("-", "_"), cm.group(0))
        if cm.group(2):
            p.put("connection.allowance_mm", _f(cm.group(2)), cm.group(0))
    mk = re.search(r"\bmark\s+([A-Za-z0-9][A-Za-z0-9_-]{0,31})", t) or re.search(r"\bcalled\s+([A-Za-z0-9][A-Za-z0-9_-]{0,31})", t)
    if mk:
        p.put("mark", mk.group(1), mk.group(0))
    mt = re.search(r"\bmaterial\s+([A-Za-z0-9][A-Za-z0-9 _.-]{0,39})", t)
    if mt:
        p.put("material", mt.group(1).strip(), mt.group(0))
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:m|metres?|meters?)\b", low):
        p.assumptions.append("a length in metres was seen: every length is read as MILLIMETRES, check them below")


# ------------------------------------------------------------------------------------------------------ space-envelope
def _space_envelope(text: str, p: Parsed) -> None:
    low = text.lower()
    sm = re.search(r"\b(level|storey|story|floor)\s+([A-Za-z0-9]+)", text, re.IGNORECASE)
    if sm:
        p.put("storey.name", f"{sm.group(1).capitalize()} {sm.group(2)}", sm.group(0))
    fm = re.search(NUM + r"\s*(m|mm)?\s*(?:floor[ -]?to[ -]?floor|f2f|ftf)", low) or re.search(r"(?:floor[ -]?to[ -]?floor|f2f)\s*[=:]?\s*" + NUM + r"\s*(m|mm)?", low)
    if fm:
        v, unit = _f(fm.group(1)), fm.group(2)
        mm = v * 1000 if (unit == "m" or (unit is None and v < 100)) else v
        p.put("storey.floor_to_floor_mm", _f(str(mm)), fm.group(0))
        if unit is None and v < 100:
            p.assumptions.append(f"floor to floor {v} had no unit: read as metres ({mm:g} mm)")
    rooms: list[dict[str, Any]] = []
    cursor = 0.0
    segments = [s for s in re.split(r"[;\n]", text) if s.strip()]
    for seg in segments:
        rm = re.match(r"\s*(?P<name>[A-Za-z][^0-9]*?)\s*[,:]?\s*" + NUM.replace("(", "(?P<w>", 1) + X + NUM.replace("(", "(?P<d>", 1) + r"\s*(?P<u>mm|m)?", seg, re.IGNORECASE)
        if not rm or re.match(r"\s*(level|storey|story|floor)\b", seg, re.IGNORECASE):
            continue
        name = rm.group("name").strip(" ,-")
        w, d, unit = _f(rm.group("w")), _f(rm.group("d")), (rm.group("u") or "").lower()
        metres = unit == "m" or (not unit and w < 100 and d < 100)
        k = 1000 if metres else 1
        if not unit and metres:
            p.assumptions.append(f"{name}: {w} x {d} had no unit: read as metres")
        tail = seg[rm.end():].lower()
        hm = re.search(NUM + r"\s*(m|mm)?\s*(?:high|clear|height|ceiling|ch)\b", tail) or re.search(r"\bh\s*[=:]\s*" + NUM + r"\s*(m|mm)?", tail)
        vm = re.search(NUM + r"\s*(m|mm)?\s*(?:void|plenum)", tail)
        room: dict[str, Any] = {"name": name, "kind": "plant_room" if "plant" in name.lower() else "room",
                                "outline": {"type": "rectangle", "x_mm": cursor, "y_mm": 0, "width_mm": w * k, "depth_mm": d * k}}
        p.understood.append({"text": rm.group(0).strip(), "field": f"rooms[{len(rooms)}].outline", "value": room["outline"]})
        if hm:
            hv, hu = _f(hm.group(1)), hm.group(2)
            room["height_mm"] = hv * 1000 if (hu == "m" or (not hu and hv < 100)) else hv
            p.understood.append({"text": hm.group(0), "field": f"rooms[{len(rooms)}].height_mm", "value": room["height_mm"]})
        if vm:
            vv, vu = _f(vm.group(1)), vm.group(2)
            room["ceiling_void_mm"] = vv * 1000 if (vu == "m" or (not vu and vv < 10)) else vv
            p.understood.append({"text": vm.group(0), "field": f"rooms[{len(rooms)}].ceiling_void_mm", "value": room["ceiling_void_mm"]})
        rooms.append(room)
        cursor += w * k + 500
    if rooms:
        p.spec["rooms"] = rooms
        if len(rooms) > 1:
            p.assumptions.append("room positions were not given: the rooms are placed left to right, 500 mm apart, from x = 0. Edit the x and y of each.")
        else:
            p.assumptions.append("the room position was not given: it is placed at x = 0, y = 0")


PARSERS = {"duct-fab": _duct_fab, "space-envelope": _space_envelope}


# ------------------------------------------------------------------------------------------------------ what is missing
def _subst(fields: list[dict[str, Any]], old: str, new: str) -> list[dict[str, Any]]:
    """The same field tree with `old` replaced by `new` in every path and discriminator key (rooms[] -> rooms[2])."""
    out = []
    for f in fields:
        g = {**f, "path": f["path"].replace(old, new)}
        if "key" in g:
            g["key"] = g["key"].replace(old, new)
        if "on" in g:
            g["on"] = g["on"].replace(old, new)
        if g["kind"] == "group":
            g["fields"] = _subst(g["fields"], old, new)
        elif g["kind"] == "list":
            g["item"] = _subst(g["item"], old, new)
        elif g["kind"] in ("choice", "conditional"):
            g["cases"] = {k: _subst(v, old, new) for k, v in g["cases"].items()}
        out.append(g)
    return out


def _walk_required(fields: list[dict[str, Any]], spec: dict[str, Any], out: list[dict[str, str]]) -> None:
    for f in fields:
        path = f["path"]
        if f["kind"] == "group":
            _walk_required(f["fields"], spec, out)
        elif f["kind"] == "conditional":
            chosen = _get(spec, f["on"])
            if chosen in f["cases"]:
                _walk_required(f["cases"][chosen], spec, out)
        elif f["kind"] == "choice":
            chosen = _get(spec, f["key"])
            if _get(spec, path) is None:
                out.append({"field": path, "question": f"What is the {f['label'].lower()}? Give its shape (a rectangle or a polygon) and its dimensions."})
            elif chosen in f["cases"]:
                _walk_required(f["cases"][chosen], spec, out)
            else:
                out.append({"field": f["key"], "question": f"Is the {f['label'].lower()} a rectangle or a polygon?"})
        elif f["kind"] == "list":
            items = _get(spec, path) or []
            if not items:
                out.append({"field": path, "question": f"Which {f['label'].lower()} are there? Give at least one."})
            for i in range(len(items)):
                _walk_required(_subst(f["item"], f"{path}[]", f"{path}[{i}]"), spec, out)
        elif f["required"] and _get(spec, path) is None:
            unit = f" in {f['unit']}" if f.get("unit") else ""
            out.append({"field": path, "question": f"What is the {f['label'].lower()}{unit}? {f['help']}".strip()})


def _get(spec: Any, path: str) -> Any:
    node = spec
    for part in re.split(r"\.|(?=\[)", path):
        if part == "":
            continue
        m = re.fullmatch(r"(\w+)?\[(\d+)\]", part)
        if m:
            if m.group(1):
                node = node.get(m.group(1)) if isinstance(node, dict) else None
            idx = int(m.group(2))
            if isinstance(node, list) and idx < len(node):
                node = node[idx]
            else:
                return None
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def missing_fields(skill: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Every required field the spec does not have yet, each with a plain-language question (also used by the agents' clarifying questions)."""
    out: list[dict[str, str]] = []
    form = build_form(get_skill(skill).schema)
    for f in form:
        if f["kind"] == "conditional":
            chosen = _get(spec, f["on"])
            if chosen is None:
                out.append({"field": f["on"], "question": "Which fitting is it: rect_to_round, rect_reducer or rect_offset?"})
    _walk_required(form, spec, out)
    seen: set[str] = set()
    return [m for m in out if not (m["field"] in seen or seen.add(m["field"]))]


def parse_shortcut(skill: str, text: str) -> Parsed:
    if skill not in PARSERS:
        raise KeyError(skill)
    p = Parsed()
    PARSERS[skill](text[:2000], p)
    p.missing = missing_fields(skill, p.spec)
    return p

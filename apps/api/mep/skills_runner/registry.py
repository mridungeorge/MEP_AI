"""The drafting skills the app can run: a fixed list, each with its spec card. A skill is a folder under `skills/` with
`SKILL.md`, `spec_card.json`, `scripts/build.py` and `validator.py` (the lane rules in skills/_template/SKILL.md).

Only the names listed in ENABLED run: a path or name coming from a request is looked up here, never joined onto a directory.
"""
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS_DIR = REPO_ROOT / "skills"
ENABLED = ("duct-fab", "space-envelope")
REQUIRES = {"duct-fab": ("cadquery", "ezdxf", "jsonschema"), "space-envelope": ("ifcopenshell", "ezdxf", "jsonschema")}
MEDIA_TYPES = {"ifc": "application/x-step", "step": "model/step", "dxf": "image/vnd.dxf", "json": "application/json"}


class UnknownSkill(KeyError):
    """No such skill is enabled."""


@dataclass(frozen=True)
class SkillInfo:
    name: str
    directory: Path
    description: str
    schema: dict[str, Any]

    @property
    def build_script(self) -> Path:
        return self.directory / "scripts" / "build.py"

    @property
    def validator(self) -> Path:
        return self.directory / "validator.py"


def get_skill(name: str, skills_dir: Path | None = None) -> SkillInfo:
    if name not in ENABLED:
        raise UnknownSkill(name)
    directory = (skills_dir or SKILLS_DIR) / name
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    return SkillInfo(name, directory, m.group(1).strip() if m else "",
                     json.loads((directory / "spec_card.json").read_text(encoding="utf-8")))


def list_skills() -> list[SkillInfo]:
    return [get_skill(n) for n in ENABLED]


def availability(name: str) -> tuple[bool, str]:
    """(True, '') when the Python packages the skill needs are installed here; otherwise why not."""
    if os.environ.get("MEP_SKILL_EXECUTOR") == "queue":          # the packages live in the worker image, not here
        return True, ""
    missing = [m for m in REQUIRES.get(name, ()) if importlib.util.find_spec(m) is None]
    return (not missing, "" if not missing else "not installed on this server: " + ", ".join(missing))

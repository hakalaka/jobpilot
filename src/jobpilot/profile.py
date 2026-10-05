"""Load the master profile and expose the facts the rest of the pipeline relies on."""
from dataclasses import dataclass
from pathlib import Path

import yaml

from .skills import normalize_all


@dataclass
class Profile:
    raw: dict

    @classmethod
    def load(cls, path) -> "Profile":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    @property
    def total_years(self) -> float:
        return float(self.raw["total_years"])

    @property
    def skills(self) -> set:
        """Every canonical skill you can defend (grouped lists flattened)."""
        out = set()
        for group in self.raw["skills"].values():
            out.update(normalize_all(group))
        return out

    @property
    def technical_skills(self) -> set:
        """Skills that are tools or techniques (excludes the 'domain' group like healthcare)."""
        out = set()
        for group, items in self.raw["skills"].items():
            if group != "domain":
                out.update(normalize_all(items))
        return out

    @property
    def learning(self) -> set:
        return set(normalize_all(self.raw.get("learning", [])))

    @property
    def bullets(self) -> dict:
        """bullet_id -> bullet dict (with the role id attached)."""
        out = {}
        for role in self.raw["experience"]:
            for b in role["bullets"]:
                out[b["id"]] = {**b, "role_id": role["id"]}
        return out

    @property
    def roles(self) -> list:
        return self.raw["experience"]

    @property
    def preferred_locations(self) -> list:
        return [x.lower() for x in self.raw.get("preferred_locations", [])]

    @property
    def target_domains(self) -> list:
        return [x.lower() for x in self.raw.get("target_domains", [])]

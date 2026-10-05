"""Helpers shared by the Databricks notebooks."""
import os

from .profile import Profile


def load_profile(catalog: str, schema: str, repo_root: str) -> Profile:
    """Use the private profile from the volume if you've uploaded it, else the committed example."""
    private = f"/Volumes/{catalog}/{schema}/private/master_profile.yaml"
    path = private if os.path.exists(private) else os.path.join(repo_root, "config", "master_profile.example.yaml")
    print(f"profile: {path}")
    return Profile.load(path)

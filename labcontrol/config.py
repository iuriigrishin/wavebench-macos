"""Public defaults with optional, untracked local overrides."""
import json
from pathlib import Path


def load_config(root):
    root = Path(root)
    config = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
    local = root / "config.json"
    if local.exists():
        config.update(json.loads(local.read_text(encoding="utf-8")))
    return config

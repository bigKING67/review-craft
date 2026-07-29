import json
from pathlib import Path


def load_reading_list(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

"""Generate translations/en.json from strings.json.

Home Assistant only resolves [%key:...] references for core integrations,
so custom integrations must ship them resolved.

Usage: python script/translations.py
"""

import json
from pathlib import Path
import re

import homeassistant

ROOT = Path(__file__).parent.parent / "custom_components" / "pley"
COMMON = json.loads((Path(homeassistant.__file__).parent / "strings.json").read_text())
KEY_RE = re.compile(r"\[%key:([a-z0-9_:]+)%\]")


def lookup(key: str) -> str:
    """Return Home Assistant's common string for a [%key:...%] reference."""
    node = COMMON
    for part in key.split("::"):
        node = node[part]
    return node


def resolve(value):
    """Resolve the [%key:...%] references in a string, or a dict of them."""
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return KEY_RE.sub(lambda m: lookup(m.group(1)), value)


strings = json.loads((ROOT / "strings.json").read_text())
(ROOT / "translations").mkdir(exist_ok=True)
(ROOT / "translations" / "en.json").write_text(
    json.dumps(resolve(strings), indent=2, ensure_ascii=False) + "\n"
)

from __future__ import annotations

from pathlib import Path
from pprint import pprint

from defcon import Font
from ufo2ft import compileTTF

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_UFO = REPO_ROOT / "sources" / "ToneOZQSPinyinKaiTrad-Regular.ufo"
OUTPUT_TTF = REPO_ROOT / Path("fonts/ToneOZQSPinyinKaiTrad.ttf")

class BuildStaticFontError(Exception):
    """Expected error raised by the static font build step."""

def build_static_font() -> dict[str, object]:
    """Build a static TTF from the primary UFO while preserving quadratic curves."""
    if not PRIMARY_UFO.exists():
        raise BuildStaticFontError(f"找不到主要 UFO: {PRIMARY_UFO}")
    OUTPUT_TTF.parent.mkdir(parents=True, exist_ok=True)
    font = compileTTF(
        Font(str(PRIMARY_UFO)),
        removeOverlaps=False,
        convertCubics=False,
        reverseDirection=False,
    )
    # format 4 may overflow during direct save; merge step rebuilds cmap from UFO.
    font["cmap"].tables = [table for table in font["cmap"].tables if table.format != 4]
    font.save(str(OUTPUT_TTF))
    return {"success": True, "output_ttf": str(OUTPUT_TTF)}

def main() -> None:
    """Run the static font build step and print a machine-friendly summary."""
    result = build_static_font()
    pprint(result)

if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path
from pprint import pprint
from typing import Any

from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_TTF = REPO_ROOT / Path("fonts/ToneOZQSPinyinKaiTraditional.ttf")
REFERENCE_METADATA = REPO_ROOT / "sources" / "reference_metadata.json"
MANIFEST_PATH = REPO_ROOT / "sources" / "reference_tables" / "manifest.json"

class ValidateBuildError(Exception):
    """Expected error raised by the build validation step."""

def collect_name_values(font: TTFont, name_id: int) -> list[str]:
    """Collect unique strings for the given name table identifier."""
    values: list[str] = []
    for record in font["name"].names:
        if record.nameID != name_id:
            continue
        try:
            text = record.toUnicode()
        except Exception:
            continue
        if text not in values:
            values.append(text)
    return values

def collect_fvar_instance_details(font: TTFont) -> list[dict[str, Any]]:
    """Collect fvar instance details including IDs and resolved names."""
    if "fvar" not in font:
        return []
    details: list[dict[str, Any]] = []
    for instance in font["fvar"].instances:
        subfamily_name_id = int(instance.subfamilyNameID)
        postscript_name_id = int(instance.postscriptNameID)
        subfamily_names = collect_name_values(font, subfamily_name_id)
        postscript_names = [] if postscript_name_id == 0xFFFF else collect_name_values(font, postscript_name_id)
        details.append(
            {
                "coordinates": {tag: float(value) for tag, value in instance.coordinates.items()},
                "subfamily_name_id": subfamily_name_id,
                "postscript_name_id": postscript_name_id,
                "subfamily_name": subfamily_names[0] if subfamily_names else "",
                "postscript_name": postscript_names[0] if postscript_names else "",
            }
        )
    return details

def ensure_no_reference_ttf() -> None:
    """Fail fast when forbidden reference_font.ttf appears in config or workspace."""
    if (REPO_ROOT / "sources" / "reference_font.ttf").exists():
        raise ValidateBuildError("Forbidden file exists: sources/reference_font.ttf")
    if MANIFEST_PATH.exists():
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "reference_ttf" in data:
            raise ValidateBuildError("Manifest must not contain reference_ttf; build must be JSON-only.")

def load_reference_metadata() -> dict[str, Any]:
    """Load the expected reference metadata generated from the source font."""
    if not REFERENCE_METADATA.exists():
        raise ValidateBuildError(f"??? reference metadata: {REFERENCE_METADATA}")
    data = json.loads(REFERENCE_METADATA.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidateBuildError("reference metadata ????")
    return data

def extract_generated_summary() -> dict[str, Any]:
    """Summarize the generated font for equivalence checks."""
    if not GENERATED_TTF.exists():
        raise ValidateBuildError(f"??? build ??: {GENERATED_TTF}")
    with TTFont(str(GENERATED_TTF)) as font:
        axes = []
        if "fvar" in font:
            axes = [{"tag": axis.axisTag, "minimum": float(axis.minValue), "default": float(axis.defaultValue), "maximum": float(axis.maxValue)} for axis in font["fvar"].axes]
        fvar_instances_detail = collect_fvar_instance_details(font)
        instances = [dict(item["coordinates"]) for item in fvar_instances_detail]
        stat_axes = []
        if "STAT" in font and hasattr(font["STAT"].table, "DesignAxisRecord"):
            design_axis_record = font["STAT"].table.DesignAxisRecord
            if hasattr(design_axis_record, "Axis"):
                stat_axes = [{"tag": axis.AxisTag, "ordering": int(axis.AxisOrdering)} for axis in design_axis_record.Axis]
        name_records = {str(name_id): collect_name_values(font, name_id) for name_id in (1, 4, 6, 16, 17, 25)}
        return {
            "glyph_count": len(font.getGlyphOrder()),
            "cmap_size": len(font.getBestCmap() or {}),
            "family_names": collect_name_values(font, 1),
            "full_names": collect_name_values(font, 4),
            "name_records": name_records,
            "tables": sorted(tag for tag in font.keys() if tag != "GlyphOrder"),
            "axes": axes,
            "instances": instances,
            "fvar_instances_detail": fvar_instances_detail,
            "stat_axes": stat_axes,
        }

def ensure_equivalent(reference: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Raise if the generated build is not functionally equivalent to the reference."""
    missing_tables = [table_tag for table_tag in reference["required_tables"] if table_tag not in generated["tables"]]
    if missing_tables:
        raise ValidateBuildError("build ?????? table: " + ", ".join(missing_tables))
    if int(reference["glyph_count"]) != int(generated["glyph_count"]):
        raise ValidateBuildError("build ?? glyph_count ????????")
    if int(reference["cmap_size"]) != int(generated["cmap_size"]):
        raise ValidateBuildError("build ?? cmap_size ????????")
    build_mode = str(reference.get("build_mode", "static")).strip().lower()
    if build_mode == "variable":
        if list(reference["axes"]) != list(generated["axes"]):
            raise ValidateBuildError("build ?? fvar axes ????????")
        if list(reference["instances"]) != list(generated["instances"]):
            raise ValidateBuildError("build ?? fvar instances ????????")
        expected_details = reference.get("fvar_instances_detail", [])
        generated_details = generated.get("fvar_instances_detail", [])
        if isinstance(expected_details, list) and expected_details:
            if list(expected_details) != list(generated_details):
                raise ValidateBuildError("build ?? fvar instance name/id ????????")
        instance_names = [str(item.get("subfamily_name", "")).strip() for item in generated_details if isinstance(item, dict)]
        unique_names = {name for name in instance_names if name}
        if len(unique_names) <= 1:
            raise ValidateBuildError("build ?? variable instances ?????????????")
    if list(reference["stat_axes"]) != list(generated["stat_axes"]):
        raise ValidateBuildError("build ?? STAT axes ????????")

    expected_name_records = reference.get("name_records", {})
    if isinstance(expected_name_records, dict):
        for name_id, expected_values in expected_name_records.items():
            generated_values = generated["name_records"].get(str(name_id), [])
            if list(expected_values) != list(generated_values):
                raise ValidateBuildError(f"build ?? nameID={name_id} ????????")

    expected_name_token = str(reference["expected_name_token"])
    all_names = list(generated["family_names"]) + list(generated["full_names"])
    if not any(expected_name_token in name for name in all_names):
        raise ValidateBuildError(f"build ?? name table ??????: {expected_name_token}")

    return {
        "success": True,
        "glyph_count": {"expected": int(reference["glyph_count"]), "generated": int(generated["glyph_count"])} ,
        "cmap_size": {"expected": int(reference["cmap_size"]), "generated": int(generated["cmap_size"])},
        "required_tables": list(reference["required_tables"]),
        "generated_tables": list(generated["tables"]),
        "axes": list(generated["axes"]),
        "instances": list(generated["instances"]),
        "fvar_instances_detail": list(generated["fvar_instances_detail"]),
        "stat_axes": list(generated["stat_axes"]),
        "name_records": dict(generated["name_records"]),
        "family_names": list(generated["family_names"]),
        "full_names": list(generated["full_names"]),
        "output_ttf": str(GENERATED_TTF),
    }

def main() -> None:
    """Run build validation and print the verification summary."""
    ensure_no_reference_ttf()
    reference = load_reference_metadata()
    generated = extract_generated_summary()
    result = ensure_equivalent(reference, generated)
    pprint(result)

if __name__ == "__main__":
    main()

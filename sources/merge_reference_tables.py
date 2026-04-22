from __future__ import annotations

import copy
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from pprint import pprint

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._f_v_a_r import NamedInstance

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_TTF = REPO_ROOT / Path("fonts/ToneOZQSPinyinKaiTraditional.ttf")
REFERENCE_TABLES_DIR = REPO_ROOT / "sources" / "reference_tables"
REFERENCE_METADATA_PATH = REPO_ROOT / "sources" / "reference_metadata.json"
MANIFEST_PATH = REFERENCE_TABLES_DIR / "manifest.json"
VARIABLE_TABLES_TTX = REFERENCE_TABLES_DIR / "variable_tables.ttx"

OTFCC_DUMP_DEFAULT = Path(r"C:/tool/otfcc/otfccdump.exe")
OTFCC_BUILD_DEFAULT = Path(r"C:/tool/otfcc/otfccbuild.exe")

class MergeReferenceTablesError(Exception):
    """Expected error raised by the merge reference tables step."""

def load_manifest() -> dict[str, object]:
    """Load the OTD manifest from disk."""
    if not MANIFEST_PATH.exists():
        raise MergeReferenceTablesError(f"Missing reference tables manifest: {MANIFEST_PATH}")
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MergeReferenceTablesError("Invalid manifest format: expected JSON object.")
    return data

def ensure_no_reference_ttf(manifest: dict[str, object]) -> None:
    """Fail fast when forbidden reference_font.ttf appears in config or workspace."""
    if (REPO_ROOT / "sources" / "reference_font.ttf").exists():
        raise MergeReferenceTablesError("Forbidden file exists: sources/reference_font.ttf")
    if "reference_ttf" in manifest:
        raise MergeReferenceTablesError("Manifest must not contain reference_ttf; build must be JSON-only.")

def resolve_tool_path(env_key: str, default_path: Path) -> Path:
    """Resolve an otfcc executable path from env var or default location."""
    candidate = os.environ.get(env_key, "").strip()
    if candidate:
        return Path(candidate)
    return default_path

def run_command(command: list[str], error_message: str) -> None:
    """Run a subprocess command and raise a friendly error when it fails."""
    try:
        subprocess.run(command, check=True, cwd=str(REPO_ROOT))
    except subprocess.CalledProcessError as exc:
        raise MergeReferenceTablesError(f"{error_message} (exit code={exc.returncode})") from exc
    except OSError as exc:
        raise MergeReferenceTablesError(error_message) from exc

def load_json(file_path: Path) -> dict[str, object]:
    """Load a JSON file from disk as an object."""
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MergeReferenceTablesError(f"Unable to read JSON file: {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise MergeReferenceTablesError(f"Invalid JSON file: {file_path}") from exc
    if not isinstance(data, dict):
        raise MergeReferenceTablesError(f"JSON root must be an object: {file_path}")
    return data

def write_json(file_path: Path, payload: dict[str, object]) -> None:
    """Write a JSON object to disk with stable formatting."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def load_table_json_payload(table_json_dir: Path, table_tag: str) -> object:
    """Load one split table JSON by table tag."""
    file_name = f"{table_tag.replace('/', '_')}.json"
    table_path = table_json_dir / file_name
    try:
        return json.loads(table_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MergeReferenceTablesError(f"Missing table JSON: {table_path}") from exc
    except json.JSONDecodeError as exc:
        raise MergeReferenceTablesError(f"Invalid table JSON: {table_path}") from exc

def merge_otd_json_from_split_tables(generated: dict[str, object], table_json_dir: Path, table_names: list[str]) -> dict[str, object]:
    """Merge selected OTD tables from split JSON files into generated JSON."""
    effective = copy.deepcopy(generated)
    merged_tables: list[str] = []
    for table_name in table_names:
        effective[table_name] = copy.deepcopy(load_table_json_payload(table_json_dir, table_name))
        merged_tables.append(table_name)
    effective["_merged_tables"] = merged_tables
    return effective

def apply_cmap_uvs(effective_json: dict[str, object], cmap_uvs_payload: dict[str, object]) -> None:
    """Apply standalone cmap_uvs mappings to the effective OTD JSON."""
    mappings = cmap_uvs_payload.get("mappings", {})
    if not isinstance(mappings, dict):
        raise MergeReferenceTablesError("Invalid cmap_uvs payload: mappings must be an object.")
    effective_json["cmap_uvs"] = {str(key): str(value) for key, value in mappings.items()}

def ensure_minimal_gdef(effective_json: dict[str, object]) -> None:
    """Ensure the output contains a minimal GDEF table when missing."""
    if "GDEF" in effective_json and isinstance(effective_json["GDEF"], dict):
        return
    effective_json["GDEF"] = {"glyphClassDef": {}}

def discover_primary_ufo_dir() -> Path:
    """Discover the primary UFO directory under sources/."""
    sources_dir = REPO_ROOT / "sources"
    ufo_dirs = sorted(path for path in sources_dir.glob("*.ufo") if path.is_dir())
    if not ufo_dirs:
        raise MergeReferenceTablesError(f"Missing UFO directory under {sources_dir}")
    return ufo_dirs[0]

def build_cmap_from_ufo(ufo_dir: Path) -> dict[str, str]:
    """Build a cmap mapping directly from UFO glyph unicode values."""
    glyphs_dir = ufo_dir / "glyphs"
    if not glyphs_dir.exists():
        raise MergeReferenceTablesError(f"Missing UFO glyphs directory: {glyphs_dir}")
    cmap: dict[int, str] = {}
    for glif_path in sorted(glyphs_dir.rglob("*.glif")):
        try:
            root = ET.fromstring(glif_path.read_text(encoding="utf-8"))
        except (ET.ParseError, OSError) as exc:
            raise MergeReferenceTablesError(f"Failed to parse glif file: {glif_path}") from exc
        glyph_name = str(root.attrib.get("name", "")).strip()
        if not glyph_name:
            continue
        for unicode_node in root.findall("unicode"):
            hex_value = str(unicode_node.attrib.get("hex", "")).strip()
            if not hex_value:
                continue
            try:
                codepoint = int(hex_value, 16)
            except ValueError:
                continue
            if codepoint not in cmap:
                cmap[codepoint] = glyph_name
    return {str(codepoint): cmap[codepoint] for codepoint in sorted(cmap.keys())}

def otd_tag_to_sfnt_tag(table_tag: str) -> str:
    """Convert OTD table tag style (e.g. OS_2) to sfnt table tag (e.g. OS/2)."""
    if table_tag == "OS_2":
        return "OS/2"
    if table_tag == "cvt_":
        return "cvt "
    return table_tag

def apply_fvar_instances_from_metadata(generated_font: TTFont) -> bool:
    """Apply fvar instances from reference metadata when split OTD JSON cannot provide them."""
    if "fvar" not in generated_font:
        return False
    existing_instances = list(getattr(generated_font["fvar"], "instances", []) or [])
    if existing_instances:
        # Do not override instances from generated/merged fonts; fallback is only for missing instances.
        return False
    if not REFERENCE_METADATA_PATH.exists():
        return False
    metadata = load_json(REFERENCE_METADATA_PATH)
    raw_instances_detail = metadata.get("fvar_instances_detail", [])
    if not isinstance(raw_instances_detail, list) or not raw_instances_detail:
        return False

    axis_defaults = {axis.axisTag: float(axis.defaultValue) for axis in generated_font["fvar"].axes}
    instances: list[NamedInstance] = []
    for raw_instance in raw_instances_detail:
        if not isinstance(raw_instance, dict):
            continue
        raw_coordinates = raw_instance.get("coordinates")
        if not isinstance(raw_coordinates, dict):
            continue
        subfamily_name_id = raw_instance.get("subfamily_name_id")
        if not isinstance(subfamily_name_id, int):
            continue
        postscript_name_id = raw_instance.get("postscript_name_id", 0xFFFF)
        if not isinstance(postscript_name_id, int):
            postscript_name_id = 0xFFFF
        instance = NamedInstance()
        instance.subfamilyNameID = int(subfamily_name_id)
        instance.flags = 0
        instance.postscriptNameID = int(postscript_name_id)
        instance.coordinates = {
            str(tag): float(raw_coordinates.get(str(tag), axis_defaults[str(tag)]))
            for tag in axis_defaults
        }
        instances.append(instance)
    if not instances:
        return False
    generated_font["fvar"].instances = instances
    return True

def merge_tables_for_variable(manifest: dict[str, object], variable_tables_ttx: Path) -> dict[str, object]:
    """Merge variable target tables by importing a fontTools TTX snapshot."""
    if not GENERATED_TTF.exists():
        raise MergeReferenceTablesError(f"Missing build output TTF: {GENERATED_TTF}")
    if not variable_tables_ttx.exists():
        raise MergeReferenceTablesError(f"Missing variable tables TTX: {variable_tables_ttx}")
    merged_tables = [str(tag) for tag in manifest.get("table_tags", []) if str(tag).strip()]
    with TTFont(str(GENERATED_TTF)) as generated_font:
        generated_font.importXML(str(variable_tables_ttx))
        if apply_fvar_instances_from_metadata(generated_font):
            if "fvar" not in merged_tables:
                merged_tables.append("fvar")
        generated_font.save(str(GENERATED_TTF))
    return {
        "success": True,
        "output_ttf": str(GENERATED_TTF),
        "variable_tables_ttx": str(variable_tables_ttx),
        "merged_tables": merged_tables,
    }

def merge_tables_for_static(manifest: dict[str, object], table_json_dir: Path, cmap_uvs_json_path: Path) -> dict[str, object]:
    """Keep static mode on otfcc round-trip while sourcing reference tables from split JSON."""
    if not GENERATED_TTF.exists():
        raise MergeReferenceTablesError(f"Missing build output TTF: {GENERATED_TTF}")

    otfccdump_path = resolve_tool_path("OTFCC_DUMP", OTFCC_DUMP_DEFAULT)
    otfccbuild_path = resolve_tool_path("OTFCC_BUILD", OTFCC_BUILD_DEFAULT)
    if not otfccdump_path.exists():
        raise MergeReferenceTablesError(f"otfccdump not found: {otfccdump_path}")
    if not otfccbuild_path.exists():
        raise MergeReferenceTablesError(f"otfccbuild not found: {otfccbuild_path}")

    generated_json_path = REPO_ROOT / str(manifest.get("generated_json", "_tmp/generated_font.otd.json"))
    effective_json_path = REPO_ROOT / str(manifest.get("effective_json", "_tmp/effective_font.otd.json"))
    generated_json_path.parent.mkdir(parents=True, exist_ok=True)
    effective_json_path.parent.mkdir(parents=True, exist_ok=True)

    run_command([str(otfccdump_path), str(GENERATED_TTF), "-o", str(generated_json_path)], f"Failed to dump generated TTF to OTD JSON: {GENERATED_TTF}")
    generated_json = load_json(generated_json_path)
    cmap_uvs_json = load_json(cmap_uvs_json_path)

    merge_table_names = [str(name) for name in manifest.get("json_merge_tables", []) if str(name).strip()]
    effective_json = merge_otd_json_from_split_tables(generated_json, table_json_dir, merge_table_names)

    cmap_strategy = str(manifest.get("cmap_strategy", "from_generated")).strip().lower()
    if cmap_strategy == "from_ufo":
        effective_json["cmap"] = build_cmap_from_ufo(discover_primary_ufo_dir())

    ensure_minimal_gdef(effective_json)
    apply_cmap_uvs(effective_json, cmap_uvs_json)
    write_json(effective_json_path, effective_json)

    run_command([
        str(otfccbuild_path),
        str(effective_json_path),
        "-o",
        str(GENERATED_TTF),
        "--keep-modified-time",
        "--keep-average-char-width",
        "--keep-unicode-ranges",
    ], f"Failed to rebuild final TTF from OTD JSON: {effective_json_path}")

    return {
        "success": True,
        "output_ttf": str(GENERATED_TTF),
        "table_json_dir": str(table_json_dir),
        "cmap_uvs_json": str(cmap_uvs_json_path),
        "generated_json": str(generated_json_path),
        "effective_json": str(effective_json_path),
        "merged_tables": list(effective_json.get("_merged_tables", [])),
    }

def merge_tables(manifest: dict[str, object]) -> dict[str, object]:
    """Dispatch merge strategy by build mode with JSON-only reference inputs."""
    ensure_no_reference_ttf(manifest)
    build_mode = str(manifest.get("build_mode", "static")).strip().lower()
    if build_mode == "variable":
        variable_ttx_rel = str(manifest.get("variable_tables_ttx", "variable_tables.ttx"))
        variable_ttx_path = REFERENCE_TABLES_DIR / variable_ttx_rel
        return merge_tables_for_variable(manifest, variable_ttx_path)

    table_json_dir_rel = str(manifest.get("table_json_dir", "tables"))
    table_json_dir = REFERENCE_TABLES_DIR / table_json_dir_rel
    if not table_json_dir.exists():
        raise MergeReferenceTablesError(f"Missing split table JSON directory: {table_json_dir}")
    cmap_uvs_json_path = REFERENCE_TABLES_DIR / str(manifest.get("cmap_uvs_json", "cmap_uvs.json"))
    if not cmap_uvs_json_path.exists():
        raise MergeReferenceTablesError(f"Missing cmap_uvs JSON: {cmap_uvs_json_path}")

    return merge_tables_for_static(manifest, table_json_dir, cmap_uvs_json_path)

def main() -> None:
    """Run merge step and print machine-friendly summary."""
    manifest = load_manifest()
    result = merge_tables(manifest)
    pprint(result)

if __name__ == "__main__":
    main()

"""
Extract CIRCLE entities on layer SPRINKLERS-COVERAGE, write centers to JSON,
remove those circles, import PP-CEILING PENDANT from a block library DXF,
and insert one block reference per center into model space.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ezdxf
from ezdxf.addons.importer import Importer
from ezdxf.lldxf import const

LAYER_COVERAGE = "SPRINKLERS-COVERAGE"
BLOCK_NAME = "PP-CEILING PENDANT"


def collect_coverage_circles(doc: ezdxf.Drawing) -> list[tuple[float, float, float]]:
    msp = doc.modelspace()
    centers: list[tuple[float, float, float]] = []
    for entity in msp.query(f'CIRCLE[layer=="{LAYER_COVERAGE}"]'):
        c = entity.dxf.center
        if hasattr(c, "x"):
            centers.append((float(c.x), float(c.y), float(c.z)))
        else:
            centers.append((float(c[0]), float(c[1]), float(c[2])))
    return centers


def delete_coverage_circles(doc: ezdxf.Drawing) -> int:
    msp = doc.modelspace()
    to_remove = list(msp.query(f'CIRCLE[layer=="{LAYER_COVERAGE}"]'))
    for entity in to_remove:
        msp.delete_entity(entity)
    return len(to_remove)


def delete_inserts_named(doc: ezdxf.Drawing, block_name: str) -> int:
    """Remove all INSERT references to block_name (any layout)."""
    key = ezdxf.validator.make_table_key(block_name)
    removed = 0
    for layout in doc.layouts:
        for insert in list(layout.query("INSERT")):
            if ezdxf.validator.make_table_key(insert.dxf.name) == key:
                layout.delete_entity(insert)
                removed += 1
    return removed


def ensure_block_replaced(
    target: ezdxf.Drawing, source: ezdxf.Drawing, block_name: str
) -> None:
    if block_name not in source.blocks:
        raise ValueError(f'Block "{block_name}" not found in block library DXF.')

    if block_name in target.blocks:
        delete_inserts_named(target, block_name)
        target.blocks.delete_block(block_name, safe=True)

    imp = Importer(source, target)
    imp.import_block(block_name, rename=False)
    imp.finalize()


def add_block_inserts(
    doc: ezdxf.Drawing,
    block_name: str,
    centers: list[tuple[float, float, float]],
    insert_layer: str,
) -> None:
    msp = doc.modelspace()
    for x, y, z in centers:
        msp.add_blockref(
            block_name,
            insert=(x, y, z),
            dxfattribs={"layer": insert_layer},
        )


def write_json(
    path: Path,
    input_path: Path,
    centers: list[tuple[float, float, float]],
) -> None:
    payload = {
        "input_path": str(input_path.resolve()),
        "layer": LAYER_COVERAGE,
        "block_name": BLOCK_NAME,
        "count": len(centers),
        "centers": [{"x": x, "y": y, "z": z} for x, y, z in centers],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replace SPRINKLERS-COVERAGE circles with PP-CEILING PENDANT inserts."
    )
    p.add_argument("--input", required=True, type=Path, help="Source DXF path")
    p.add_argument(
        "--blocks",
        type=Path,
        default=Path("blockssprinkler.dxf"),
        help="DXF containing block definitions (default: blockssprinkler.dxf)",
    )
    p.add_argument("--output", required=True, type=Path, help="Output DXF path")
    p.add_argument(
        "--json",
        type=Path,
        default=None,
        help="JSON output path (default: <input_stem>_sprinkler_centers.json)",
    )
    p.add_argument(
        "--insert-layer",
        default=LAYER_COVERAGE,
        help=f"Layer for new INSERT entities (default: {LAYER_COVERAGE})",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    blocks_path = args.blocks.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    json_path = (
        args.json.expanduser().resolve()
        if args.json
        else input_path.with_name(f"{input_path.stem}_sprinkler_centers.json")
    )

    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 1
    if not blocks_path.is_file():
        print(f"error: block library not found: {blocks_path}", file=sys.stderr)
        return 1

    try:
        doc = ezdxf.readfile(str(input_path))
    except const.DXFStructureError as e:
        print(f"error: cannot read input DXF: {e}", file=sys.stderr)
        return 1

    centers = collect_coverage_circles(doc)
    if not centers:
        print(
            f"error: no CIRCLE entities on layer {LAYER_COVERAGE!r} in model space.",
            file=sys.stderr,
        )
        return 1

    write_json(json_path, input_path, centers)

    removed = delete_coverage_circles(doc)
    if removed != len(centers):
        print(
            f"warning: removed {removed} circles but collected {len(centers)} centers.",
            file=sys.stderr,
        )

    try:
        src = ezdxf.readfile(str(blocks_path))
    except const.DXFStructureError as e:
        print(f"error: cannot read block library DXF: {e}", file=sys.stderr)
        return 1

    try:
        ensure_block_replaced(doc, src, BLOCK_NAME)
    except (ValueError, const.DXFKeyError, const.DXFBlockInUseError) as e:
        print(f"error: block import failed: {e}", file=sys.stderr)
        return 1

    add_block_inserts(doc, BLOCK_NAME, centers, args.insert_layer)

    try:
        doc.saveas(str(output_path))
    except OSError as e:
        print(f"error: cannot save output: {e}", file=sys.stderr)
        return 1

    print(
        f"Wrote {len(centers)} centers to {json_path}\n"
        f"Removed {removed} circle(s) on {LAYER_COVERAGE!r}\n"
        f"Saved {output_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

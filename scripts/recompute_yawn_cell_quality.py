#!/usr/bin/env python3
"""Refresh yawn cell quality metadata (manifest/meta/store) from cell bundles."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.yawn_cell_quality import refresh_yawn_quality_metadata  # noqa: E402
from re1_rl.yawn_rails_sync import yawn_rails_root  # noqa: E402


def main() -> int:
    changes = refresh_yawn_quality_metadata(ROOT)
    print(f"Updated {len(changes)} cells under {yawn_rails_root(ROOT)}")
    for c in changes:
        if c.get("error"):
            print(f"  cp{c['idx']:02d}: ERROR {c['error']}")
            continue
        ammo_delta = int(c["new_ammo"]) - int(c["old_ammo"])
        flag = ""
        if c["old_beats_new"] and not c["beats_self"]:
            flag = " (old lex > new)"
        elif c["beats_self"] and not c["old_beats_new"]:
            flag = " (incumbent stronger after recompute)"
        print(
            f"  cp{c['idx']:02d} {c.get('checkpoint_id')}: "
            f"ammo {c['old_ammo']} -> {c['new_ammo']} ({ammo_delta:+d}){flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

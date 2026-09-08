# Planner-loyal stretch kill audit + quality lexicon

Each successful **plNN** mint records what the episode thinks it killed on that planner stretch. Almanac totals also feed the mint **quality** lexicon (see below).

Implementation: `planner_loyal_kill_audit()`, `assemble_planner_loyal_quality()` in [`re1_rl/planner_loyal_cells.py`](../re1_rl/planner_loyal_cells.py).

## Quality lexicon (11 dims)

Lexicographic mint overwrite (higher better except already-negated dims):

```text
(hp, total_kills, ammo, healing, slots, poison, -ink, -box, -frames,
 stretch_kills, hop_score_milli)
```

| Index | Dim | Source at mint |
|------:|-----|----------------|
| 0 | `hp` | `compute_quality` |
| 1 | `total_kills` | `kills.almanac_total` |
| 2 | `ammo` | HG-eq (was formerly quality[1]) |
| 3 | `healing` | |
| 4 | `slots` | |
| 5 | `poison` | |
| 6 | `-ink` | |
| 7 | `-box` | |
| 8 | `-frames` | |
| 9 | `stretch_kills` | `kills.almanac_stretch` |
| 10 | `hop_score_milli` | `round(S * 1000)` from hop meters / breakdown |

Unknown backfill sentinel: **`-99999`** (`PLANNER_LOYAL_QUALITY_UNKNOWN`). Stitch script: `_tmp/_stitch_planner_loyal_quality_11.py`.

Yawn / go-explore stay on the 8-dim tuple; only planner-loyal mint compare uses this lift.

## Two ledgers

| Field | Source | Meaning |
|-------|--------|---------|
| **`kills_paid`** | Reward detector (`leg_kills_by_room` / `last_claimed_leg_kills`) | Kills that paid `+0.24` on knife/attack steps during this stretch |
| **`almanac_stretch`** | World ledger (`enemies_killed_by_room`) minus predecessor cell | Typed kills (room + enemy type) accumulated since the prior pl cell |

They often disagree. **`almanac_stretch`** is the lineage ground truth for “did this stretch add zombies/dogs/etc.” **`kills_paid`** can spike when combat rewards fire without a clean almanac delta, or when reward-side counting races the mint snapshot.

Healthy armor-room stretches (pl79→pl80) should show **`almanac_stretch=0`**. Most mints fleet-wide should be **0**, with occasional **1–2** on fight hops; **3** is rare; **>3** on `almanac_stretch` is worth investigating.

## What gets stored

On every mint:

1. **Worker log** — one line:
   ```text
   [planner_loyal] minted pl57 chunk=cp05_shield_key step=51 room=201 final=0 start=1 q=[...]
   hop=1.23 kills_paid=9 10A:2,11A:1,202:2,204:2,207:2 almanac_stretch=2 202:zombie=2 almanac_total=12
   ```
2. **`meta.json`** → `"quality": [...]` (11 dims), `"kills": { ... }`, `"hop_score": <float|null>`
3. **`cell.sidecar.json`** → `planner_loyal.kills` (same kill block)

Full audit object:

| Key | Type | Notes |
|-----|------|-------|
| `paid_stretch` | int | Sum of `kills_paid` |
| `paid_stretch_by_room` | `{room: count}` | Reward-side stretch kills |
| `paid_episode` | int | Episode-total paid kills (not cleared on mint) |
| `paid_episode_by_room` | `{room: count}` | |
| `almanac_stretch` | int | Sum of typed kills this stretch vs predecessor |
| `almanac_stretch_by_room` | `{room: {type: count}}` | e.g. `202: {zombie: 2}` |
| `almanac_total` | int | Cumulative typed kills in episode almanac |
| `almanac_total_by_room` | `{room: {type: count}}` | Lineage; not cleared on mint |

After mint, **`close_planner_loyal_stretch()`** clears paid stretch counters so the next pl hop starts fresh. Almanac totals stay cumulative.

Existing cells minted before kill audit may still have sidecar `planner_loyal.kills` for stitch backfill; hop milli is usually unknown (`-99999`) until remint.

## How to read a stretch

- **`pl79` → `pl80`**: expect `almanac_stretch=0` (puzzle hop, no new kills).
- **Fight hops** (e.g. pl54→pl55): `almanac_stretch` may be 1–2 zombies/dogs; `kills_paid` may be higher if SG was spent.
- **`almanac_total`**: running typed kill ledger for the whole episode from reset tip; predecessor cell sidecar seeds the delta for `almanac_stretch`.

## Where to grep

| Host | Log path |
|------|----------|
| pking | `data/logs/worker_pking_planner_loyal.log` |
| WH1 | `D:/re1_rl/data/logs/worker_workhorse1_planner_loyal.log` |
| WH2 | `C:/Users/sshuser/re1_rl/data/logs/worker_wh2_planner_loyal.log` |
| WH3 | `C:/Users/sshuser/re1_rl/data/logs/worker_workhorse3_planner_loyal.log` |

Quick patterns:

```powershell
findstr /C:"[planner_loyal] minted pl" data\logs\worker_pking_planner_loyal.log
findstr /C:"almanac_stretch=" data\logs\worker_pking_planner_loyal.log
```

Audit script (local):

```powershell
python _tmp/_audit_stretch_kills_table.py
```

Stitch 11-dim quality (local or after fleet sync):

```powershell
python _tmp/_stitch_planner_loyal_quality_11.py --dry-run
python _tmp/_stitch_planner_loyal_quality_11.py
```

## Fleet snapshot

Generated **2026-09-01 15:38** from mint log lines (`kills_paid` / `almanac_stretch` since ~09:10 restart). WH1 SSH timed out at generation time.

### Per-host histogram

| Host | Mints | kills_paid | almanac_stretch | paid>3 | alm>3 |
|------|------:|------------|-----------------|--------|-------|
| pking | 3 | 2 (1), 7 (1), 9 (1) | 0 (2), 2 (1) | 2 | — |
| WH1 | — | — | — | — | — |
| WH2 | 7 | 0 (2), 2 (1), 5 (1), 7 (1), 9 (2) | 0 (3), 2 (4) | 4 | — |
| WH3 | 4 | 0 (2), 2 (2) | 0 (4) | — | — |
| **fleet** | **14** | **0 (4), 2 (4), 5 (1), 7 (2), 9 (3)** | **0 (9), 2 (5)** | **6** | **—** |

Histogram cells read as **value (count)** — e.g. `2 (4)` = four mints with `kills_paid=2`.

### Per-mint rows

| Host | Cell | kills_paid | almanac_stretch |
|------|------|----------:|----------------:|
| pking | `pl31` | 2 | 0 |
| pking | `pl56` | 7 | 0 |
| pking | `pl57` | 9 | 2 |
| WH2 | `pl39` | 2 | 0 |
| WH2 | `pl50` | 0 | 0 |
| WH2 | `pl54` | 5 | 2 |
| WH2 | `pl55` | 7 | 2 |
| WH2 | `pl57` | 9 | 2 |
| WH2 | `pl57` | 9 | 2 |
| WH2 | `pl67` | 0 | 0 |
| WH3 | `pl37` | 2 | 0 |
| WH3 | `pl39` | 2 | 0 |
| WH3 | `pl42` | 0 | 0 |
| WH3 | `pl67` | 0 | 0 |

### Notes

- **No `pl80` mints** in this window; pl79 pin ~50% (`reset tip=pl79`).
- **`almanac_stretch` max = 2** across all 14 mints; **never >3**.
- **`kills_paid > 3`** on 6 mints (pl54–pl57 fight remints on pking/WH2); WH3 max paid = 2.
- WH2 **`pl57` reminted twice** (both paid=9, almanac=2).

Re-run `_tmp/_audit_stretch_kills_table.py` for an updated snapshot.

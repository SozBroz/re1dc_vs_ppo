# Planner-loyal ammo usage by hop

Audit of **where the fleet spends ammo** on planner-loyal stretches. Companion to [`planner_loyal_kills.md`](planner_loyal_kills.md).

Ammo in the planner-loyal quality lexicon is **handgun-round equivalents** (damage-weighted; 1 shotgun shell ≈ 6 HG) at **`quality[2]`** after the 11-dim stitch (`total_kills` is `quality[1]`). Legacy 8-dim cells stored ammo at `quality[1]` until stitched. It includes on-person **and** box ammo. Spend on a hop is:

```text
predecessor_cell.ammo + expected_pickup_hg − mint.ammo
```

Expected pickup is 15 for a handgun pile, 43 for the living-room shotgun / a 7-shell pile. That pickup term is approximate (pile size and loaded qty vary).

This is **audit-only**. Ammo spend already taxes PPO (`ammo_spend` / `ammo_waste`). Do not invent new paid events from this table.

## Snapshot

Generated **2026-09-06** from 731 `[planner_loyal] minted pl` lines on C-RE1 worker logs plus installed `states/planner_loyal/cells` (pl00–pl55).

| Host | Mint lines |
|------|----------:|
| pking | 22 |
| WH1 | 105 |
| WH2 | 209 |
| WH3 | 395 |
| **fleet** | **731** |

Re-run:

```powershell
python _tmp/_collect_pl_mints.py
python _tmp/_parse_pl_ammo_writeup.py
```

## Where live remints burn ammo

**Times minted** = how many successful finishes of that hop we averaged (not a game stat).

Ammo is the inventory score in handgun-round equivalents (one shotgun shell counts as about six).

| Hop | Times minted | Ammo at start | Ammo at finish (avg) | Best finish | Worst | Rounds dumped |
|-----|-------------:|--------------:|---------------------:|------------:|------:|--------------:|
| pl51 place star crest (11A dogs) | 12 | 71 | 39 | 63 | 20 | 32 |
| pl52 leave 11A back to 10A | 38 | 63 | 45 | 63 | 20 | 18 |
| pl54 climb 10B to 207 | 14 | 63 | 45 | 63 | 33 | 18 |
| pl41 walk into the gallery | 9 | 83 | 66 | 73 | 52 | 17 |
| pl42 first gallery portrait | 21 | 73 | 63 | 73 | 22 | 10 |
| pl38 leave living room 116 | 16 | 83 | 75 | 83 | 27 | 8 |
| pl50 walk 10A to 11A | 22 | 71 | 64 | 71 | 40 | 7 |

**pl55** (207→204 C passage) is the highest mean spend (42) but only **n=6**. Champion already dropped 63→42 on that hop; live remints average **21**.

## Three clusters

1. **Star-crest dog hallway (pl50–pl52)** — current leak. Placing the crest and walking back through 11A is where most ammo disappears. Almanac kills stay ~0–1.
2. **Stairs / C passage (pl54–pl55)** — 10B→207 and 207→204. Champion pl55 is already a 21-round drop; live is worse.
3. **Gallery enter + first portrait (pl41–pl42)** — still a spray zone (Sep 5 writeup). No longer the worst cluster.

## Champion path (installed cells)

Hops where the **winning** cell itself spent > 8 HG-eq vs the previous cell:

| Cell | Hop | Pred → champ | Net spend |
|------|-----|-------------:|----------:|
| pl55 | 207→204 C passage | 63 → 42 | 21 |
| pl34 | 10A→109 | 55 → 40 | 15 |
| pl27 | 10A→10B | 70 → 56 | 14 |
| pl41 | 10A→117 gallery enter | 83 → 73 | 10 |
| pl51 | place star crest | 71 → 63 | 8 |

pl34’s installed cell is a **depleted** champion (40). Live remints often beat it (mean 48), so deficit vs champ goes negative even though they still spend vs pl33.

## Spray (paid kills, almanac 0)

| Cell | Hop | n | Paid | Almanac |
|------|-----|--:|-----:|--------:|
| pl34 | 10A→109 | 8 | 2.1 | 0 |
| pl35 | 109→115 | 17 | 1.7 | 0 |
| pl52 | 11A→10A | 38 | 1.3 | 0 |
| pl42 | gallery portrait 1 | 21 | 1.1 | 0 |

Combat reward fired; the world ledger added no typed kill.

## What this is not

- Not raw clip dumps. Quality folds shotgun/acid/box into HG-eq.
- Not a reason to add elastic ammo bonuses to PPO.
- Later hops (pl56+) have no installed predecessor on this box yet; late-log mints (pl91, pl109) are single-digit n and omitted from the frequent table.

# Mansion-exit footage audit + room-segment map

Companion to [`planner_loyal_cells.md`](planner_loyal_cells.md) and
[`data/planner_loyal_room_segments.json`](../data/planner_loyal_room_segments.json).

Generated for the mansion→guardhouse cut (**through `pl200` `courtyard_enter` /
`11B→300` FIRST HOUSE OUT**). Live cells are C-RE1 `cell.pst`; BizHawk twins
live under `backups/planner_loyal_bizhawk_20260904/` (130 cells, Sep 4 2026).

## BizHawk vs live numbering

Sep 2026 remint shifted the spine: live `pl00` = dining fresh start (no emblem);
opening remint `pl01`…`pl06`; shield-key step 0 mints `pl07`. BizHawk archive
still has the pre-remint map (`pl01` = Kenneth, no separate emblem cell).

| Live | BizHawk (approx) | Notes |
|------|------------------|-------|
| `pl00` fresh_start_105 | `pl00` fresh_start_105 | Same tip |
| `pl01` emblem_105 | *(baked into pre-Kenneth)* | Live-only opening remint |
| `pl02` kenneth_104 | `pl01` kenneth_104 | −1 offset starts here |
| `pl07` 106→105 | `pl06` cp05 step01 | Live tip after lockpick |
| `plNN` (N≥7) | `pl(N−1)` | Until BizHawk archive ends (~pl130) |

Use **live** `plNN` for all new footage / segment work.

## Pickups & objectives to collect (mansion → courtyard)

Room-exit segments group consecutive PLs so additive `S_room = Σ S_hop` absorbs
time pressure for the whole visit. Prototype segments (flag default) are marked ★.

| Segment | PLs | Room(s) | Collect / finish |
|---------|-----|---------|------------------|
| ★ `opening_emblem_to_tea` | pl01–pl02 | 105→104 | Wooden emblem; leave dining into tea (Kenneth) |
| `opening_return_to_lockpick` | pl03–pl06 | 105→106→203→106 | Return path; lockpick tip |
| (solo) | pl07 | 106→105 | Walk into dining |
| `tea_clips_leave_bar` | pl08–pl11 | 104→10F | Two handgun clips; leave tea into bar |
| `bar_piano_gold_swap` | pl12–pl15 | 10F | Music notes, piano, gold emblem, alcove swap |
| `dining_fireplace_shield` | pl16–pl19 | 104→105 | Fireplace place + **shield key** |
| `gallery_to_l_enter` | pl20–pl21 | 106→107 | Main hall → gallery |
| ★ `l_hallway_bullets` | pl22–pl24 | 108→109 | Enter L, **+15 HG clip**, leave to trap |
| `trap_herb_to_back` | pl25–pl26 | 109→10A | Green herb; leave to back passage |
| `stairs_herb_chemical_box` | pl27–pl31 | 10B→118 | Stairs herb, chemical, leave_118 box |
| (traverses) | pl32–pl34 | … | Return toward trap room |
| `shotgun_living_room` | pl35–pl37 | 115→116 | **Shotgun** |
| (traverses) | pl38–pl40 | … | Back to large gallery |
| (PL-by-PL) `gallery` | pl41–pl49 | 117 | Six portraits + EOL + **star crest** (each PL own episode + tape) |
| `place_star_crest` | pl51–pl52 | 11A | Place star crest |
| (traverses / 2F run) | pl53–pl61 | … | Up to greenhouse |
| `greenhouse_armor_herbs` | pl62–pl68 | 10C | Pump, **armor key**, 4 bench herbs |
| `vacant_ammo` | pl71–pl73 | 102 | Clip + shells |
| `save_room_box` | pl75–pl76 | 100 | leave_100 box |
| (PL-by-PL) `armor room` | pl82–pl85 | 205 | Both vents + **sun crest** (each PL own episode + tape) |
| `place_sun_crest` | pl91–pl92 | 11A | Place sun crest (Richard timer) |
| `dining_2f_statue_jewel` | pl98–pl103 | 202→105 | Push statue; **blue jewel** |
| `tiger_wind_crest` | pl110–pl112 | 10D | Jewel in tiger; **wind crest** |
| `employee_ammo` | pl114–pl116 | 10E | Clip + shells |
| (tea unlock path) | pl117–pl120 | 103→106 | Unlock 103→104; to dressing |
| `dressing_wardrobe_loot` | pl121–pl126 | 111→112 | Clip, shells, 2 green herbs |
| `place_wind_crest` | pl133–pl134 | 11A | Place wind crest |
| (Yawn staging box/bedroom) | pl137–pl152 | 118/209… | Box ops + bedroom loot segment |
| `bedroom_loot` | pl143–pl146 | 209 | Red herb, clip, **lighter** |
| `study_puzzle_rounds` | pl156–pl160 | 20A | Insect/tank/cupboard + explosive rounds |
| (Yawn / moon crest path) | pl165–pl196 | … | Through **place_moon_crest** |
| `courtyard_shed_exit` | pl197–pl200 | 11B→300 | Stepladder, **square crank**, **leave mansion** |

Solo traverse PLs not listed stay one-hop episodes under hop score.

## Last footage artifacts (do not re-capture plates)

| Artifact | Path |
|----------|------|
| Crystals A/V batch (2026-08-23) | `data/footage/crystals_full/` (86 OK / 36 fail) |
| Room camera plates 104/105/108 | `data/room_cameras/{104,105,108}/` |
| Layered masks | `data/room_cameras/layered_mask_manifest.json` |
| BizHawk PL archive | `backups/planner_loyal_bizhawk_20260904/` |

## How additive room scoring works

1. Keep per-PL hop score (`re1_rl/planner_hop_score.py`) unchanged.
2. With `RE1_PLANNER_ROOM_SEGMENTS=1`, hops inside a segment share one episode and
   one hard timeout (`n_hops × 21600` frames).
3. Each hop success settles `S_hop` into `RoomSegmentTracker`; mid-hops mint the
   cell but do **not** end the episode.
4. Segment exit sets learning target `hop_score = S_room = Σ S_hop` and records
   best under `data/planner_loyal_room_segment_best.json`.
5. Default `RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY=1` chains only ★ segments.

## RE1-C vs BizHawk for this footage

See [`mansion_exit_av_verdict.md`](mansion_exit_av_verdict.md): train/chain on
C-RE1; film A/V on BizHawk (recomp has no YouTube-grade A/V dump on the plugin path).

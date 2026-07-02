# RE1 RL Route Data — Sources (v0.1)

Generated 2026-07-01 for Jill Any% (PS1 Director's Cut / PC-classic parity).

## URLs actually used

| URL | Used for |
|-----|----------|
| https://www.tapatalk.com/groups/residentevil123/re1-rooms-list-bss-pak-rdt-t3197.html | **Primary** room ID table (stage 1XX–5XX, debug names). Direct fetch blocked (Cloudflare 403); content recovered via search-engine cached excerpts. |
| https://www.tapatalk.com/groups/residentevil123/re1-room-list-t1204.html | Secondary area-ordered room names (mansion / courtyard / guardhouse / lab); cross-check only. |
| https://www.tapatalk.com/groups/residentevil123/rdt-file-format-t2683.html | RDT naming convention `roomSXX0.rdt` (stage + room hex). |
| http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997) | RDT file format; stage/room ID semantics. |
| https://www.speedrun.com/residentevil/guides/7txue | WitchRain **Jill Any% Item Order** (pickup sequence). |
| https://www.speedrun.com/residentevil/guides/vtrks | clix_gaming **PC Jill Any% NMG** guide (metadata only — full step text not rendered by fetch). |
| http://alexfung.info/favorite/game/Resevil.htm | Complete Chris/Jill walkthrough; Jill mansion shortcut, Plant 42, Barry passcode route, lab flow. |
| https://strategywiki.org/wiki/Resident_Evil/Walkthrough | Character differences; general mansion flow confirmation. |
| https://github.com/biorand/classic | Room ID spot-checks from changelog/issues (e.g. 301 bees, 400 statue, 405 snakes, 406 ladder). |
| https://github.com/deserteagle417/RE1-Autosplitter | Segment names (Mansion 1, Courtyard, Guardhouse, Black Tiger, Underground, Tyrant). |
| https://www.evilresource.com/resident-evil/maps/guardhouse-residence-contents | Guardhouse room names (001/002/003, Plant 42, etc.). |
| https://residentevil.fandom.com/wiki/Courtyard/Garden_with_a_fountain | Fountain / medal / lab elevator lore. |
| https://residentevil.fandom.com/wiki/Laboratory_entrance | Lab B1 helipad passage gating. |
| https://residentevil.fandom.com/wiki/Way_to_the_heliport | Endgame helipad route naming. |

## Not fetched (blocked or empty)

- TapTalk thread HTML (403 Cloudflare) — relied on indexed snippets.
- speedrun.com guide bodies (shell pages without tutorial text in automated fetch).
- web.archive.org mirror of TapTalk (403).

## Unverified / needs human with game running

### `rooms.json`

1. **Stage 2 tail (213–21C)** — `FRONT ELEVATOR` through `UNDER KITCHEN`: names from TapTalk list order; hex IDs assigned sequentially after `212` (not shown in accessible mirrors).
2. **Stage 4 (401–411)** — all guardhouse sub-rooms except `400 ENTER PASSAGE`: sequential assignment from table order; biorand references `405`/`406` but does not publish official names.
3. **Stage 5 (501–515)** — lab interior: `501 HELIPORT PASSAGE` confirmed by search synthesis; `500` confirmed; `502–515` sequential from table order.
4. **`311 ROTATING HOLE`** — inferred from RE1 Room List (t1204) courtyard B1 ordering; no hex in primary table.
5. **`110`, `119`, `200`** — marked `N/A` in debug table (may be unused RDT slots).

### `route_jill_anypct.json`

1. **Exact room ID per pickup** — many mansion pickups (ammo room, Kenneth corridor, doom books) mapped to best-guess IDs.
2. **`102` Rebecca / serum room** — Jill line may differ from Chris Rebecca encounter room ID.
3. **Guardhouse `409` vs `408`** for V-Jolt / chemicals room (Jill code 3:45).
4. **Mansion 2F MO disk path** — Any% uses Barry passcode + security door (`213` guess) vs full basement (`21A`–`21C`); route lists both with flags.
5. **Lab passcode rooms** — which MO disk reader lives in `508` vs maze/power rooms needs in-game confirmation.
6. **Medal rooms `506`/`507`** — wolf/eagle medal hex IDs inferred from stage-5 ordering.
7. **Start room** — Jill intro may load `105` before `106`; first waypoint uses dining emblem pickup.

### Recommended verification pass

1. Run RE1 with a room logger (BioRand debug, RE1 autosplitter memory reads, or `roomSXX0.rdt` filename overlay).
2. Walk Jill Any% once, logging `(stage, room_hex)` at each WitchRain item pickup.
3. Patch `rooms.json` stage 4/5 hex columns from extracted `room4*.rdt` / `room5*.rdt` filenames on disk.

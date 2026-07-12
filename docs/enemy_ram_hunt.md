# Enemy RAM hunt protocol (PS1 SLUS-00551)

**Goal:** locate the live enemy entity table so the egocentric observation vector can expose **top-5 enemy relative positions** — the audio substitute for off-camera threats ("hearing"). The encoder already reserves proprio slots; addresses are the blocker.

**Disc / emu:** Director's Cut `SLUS-00551`, BizHawk 2.11.1 + `lua/re1_client.lua`, bridge via `BizHawkClient`.

---

## MediaKite ASL lead (low confidence on PS1)

`data/RE1aio.asl` (deserteagle417 autosplitter, MediaKite / GOG builds) lists six enemy **HP** ushorts:

| Slot | GOG offset | Spacing from prev |
|------|------------|-------------------|
| Enemy1 | `0x8353BC` | — |
| Enemy2 | `0x835548` | `0x18C` (396) |
| Enemy3 | `0x8356D4` | `0x18C` |
| Enemy4 | `0x835860` | `0x18C` |
| Enemy5 | `0x8359EC` | `0x18C` |
| Enemy6 | `0x835B78` | `0x18C` |

**Stride hypothesis:** six consecutive slots, **`0x18C` bytes apart**, each struct likely holding coordinates, type, HP, alive/state.

**Linear-map candidate (suspect):** this repo's verified GOG→PS1 map is `PS1 = GOG - 0x7211C0` (anchor: HP `0x7E636C` → `0x800C51AC`). Applied blindly to Enemy1:

```
0x8353BC - 0x7211C0 = 0xC141FC  →  PS1 bus 0x801141FC
```

**Why it's suspect:** the ASL enemy offsets sit in a **MediaKite-specific** block (same numeric offsets pasted into English GOG, Japanese GOG, and REbirth tables) while player HP/room IDs use per-build offsets. The enemy pointers may be **heap-relative on PC**, not part of the save-aligned block we mapped. Treat `0x801141FC` as a **search hint only** — full-RAM kill diff is ground truth.

---

## Prerequisites

1. EmuHawk running RE1 DC **Original** (not Advanced/Arranged — different HP block).
2. Lua client connected to the Python hunt script (`--port` must match).
3. Savestate with Jill in player control, single-enemy room preferred.

**Recommended rooms**

| Room | ID | Why |
|------|-----|-----|
| Tea room | 104 | Single Kenneth zombie on approach — clean kill diff |
| Trap room | 115 | Isolated enemy, room geometry stable |

Load `states/jill_control_fresh.State` or a custom state positioned in the target room.

---

## Step-by-step: `scripts/hunt_enemy_ram.py`

```text
D:\re1_rl\venv\Scripts\python.exe scripts\hunt_enemy_ram.py --port 5555
```

### Phase A — alive snapshot

1. Script loads savestate and waits.
2. Operator positions Jill with **one living enemy** in frame (or known slot).
3. Press **Enter** → script reads full MainRAM (`0x80000000`, 2 MiB) in `0x10000` chunks.

### Phase B — dead snapshot

1. Operator kills that enemy (knife/gun — avoid room reload).
2. Press **Enter** → second full snapshot.

### Phase C — diff report

Script clusters changed bytes into runs and ranks clusters by:

- distance to candidate `0x801141FC`
- `u16` transitions to `0` (HP drain / death)
- single-byte state flips (`1→0` alive flags)

Review top clusters in the console; note any run spaced `0x18C` apart.

### Phase D — second session (optional)

Answer `y` to repeat in another room/enemy type. Script **intersects** cluster addresses across sessions — persistent enemy-table hits survive room-specific noise.

### Phase E — struct probe

Enter a base address (default `0x801141FC`) or your confirmed cluster start. Each **Enter** dumps **6 slots × `0x18C`** with heuristic `s16` coord candidates (magnitude 1000–33000, compare to `PLAYER_X`/`PLAYER_Z`) and `u16`/`u8` fields. Watch values while the enemy walks.

**Camera-cut persistence:** when `CAM_ID` (`0x800C8662`) changes within the same `ROOM_ID`, enemy slot addresses and coordinates should **remain stable** (same room load). If coords reset or slots clear, the base is wrong or enemies re-spawn on cut.

### Output

`data/enemy_ram_hunt_<timestamp>.json` — sessions, ranked clusters, intersection, struct probe readings, chosen base.

**Struct-only mode:**

```text
python scripts/hunt_enemy_ram.py --base 0x80114288 --probe-only --port 5555
```

---

## Cross-checks

- **REviewer** (speedrun overlay): if runnable alongside BizHawk, compare live enemy HP count against probe `u16` fields.
- **Damage-without-kill:** shoot enemy once, Enter-probe; HP-like `u16` should drop but slot stay active.
- **Second enemy type:** Crimson Head vs zombie — `type` byte should differ at same struct offset.

---

## Interaction-prompt hunt (brief)

**Goal:** byte(s) set when the engine would show **"Press X"** at a door or item — distinct from `MESSAGE_FLAG` (`0x800C8665`, modal text window, bit `0x80`).

```text
python scripts/hunt_interaction_prompt.py --port 5555 --rounds 3
python scripts/hunt_interaction_prompt.py --fast   # 0x800C0000-0x800D0000 only
```

Alternating AWAY / AT snapshots; reports bytes **consistently** different every AT vs every AWAY, excluding player block `0x800C5100–0x800C5200` and timers `0x800C8670–0x800C8680`. Prints `MESSAGE_FLAG` each snapshot for correlation.

---

## SCD work-flag hunt (brief)

**Goal:** per-room event bits near `DOOR_FLAGS` (`0x800C86B4`) — emblem placed, Barry lockpick, puzzles solved.

```text
python scripts/hunt_scd_flags.py --port 5555
python scripts/hunt_scd_flags.py --full   # entire MainRAM
```

Before/after Enter pairs; reports single-bit flips in `0x800C8600–0x800C8800` by default. Name confirmed flags interactively → merged into `data/scd_work_flags.json` (existing entries preserved).

---

## RESULTS (confirmed 2026-07-12)

| Field | Value |
|-------|-------|
| **Enemy table base (PS1 bus)** | `0x800C532C` (Gameshark zombie HP / first-zombie infinite) |
| **Slot stride** | `0x18C` |
| **Slot 0 offset: HP** | `+0x0` (`u16`) |
| **Max active slots** | 6 (same as ASL) |
| **Verified rooms** | 202 (zombies); **108 dog** QuickSave1 2026-07-12 — slot0 HP 19→0 one beretta, +kill reward PASS |
| **Evidence** | Live beretta: ~12 HP/hit; kill clears slot; `probe_zombie_fire_rewards.py` PASS. Rejected `0x801141FC` (never moved on hits). |

Coords / type / alive bytes still **unmapped** (HP-only decode for combat rewards).

**Interaction prompt byte(s):**

| Address | AWAY value | AT value | Notes |
|---------|------------|----------|-------|
| | | | |

**SCD flags:** see `data/scd_work_flags.json`.

---
name: re1-muse-planner
description: >-
  Invoke Muse Glimmer as the RE1 Phase-1 route planner: tear down learner if
  needed, start llama-server on WH3 :8000, build the almanac packet
  (build_phase1_context + tip overlay), POST chat/completions, validate, log.
  Use when the user asks Muse what to do next, to author the next planner-loyal
  chunk, or how the planner almanac is supplied.
---

# RE1 Muse planner (Phase 1)

## Role

**Muse Glimmer 30B** (`muse-glimmer` on WH3 `:8000`) authors the next training
chunk. Code **validates fail-closed** and never repairs hops.
Scope: mansion four crests through `place_moon_crest`. Not courtyard/residence.

## When to apply

- User asks Muse / the planner what to do next in RE1
- Authoring `next_chunk` after a `chunk_final` tip (e.g. `pl134` / `place_wind_crest`)
- Questions about how the **almanac** is built or given to Muse

## Host / model

| Item | Value |
|------|--------|
| Host | **workhorse3** `sshuser@192.168.0.229` |
| Repo | `C:\Users\sshuser\re1_rl` |
| Endpoint | `http://127.0.0.1:8000/v1/chat/completions` |
| Health | `http://127.0.0.1:8000/health` |
| Model alias | `muse-glimmer` |
| GGUF | `_tmp/muse-glimmer/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` |
| Start (learner **down**, **SSH-safe**) | `_tmp/wh3_muse_wmi_start.ps1` → `_tmp/wh3_muse_llama_server.cmd` |
| Start (interactive / same session) | `_tmp/_wh3_muse_start_full.ps1` |
| Start (learner **up**) | `_tmp/_wh3_muse_start_coexist.ps1` + **slim** packet |
| Context | `-c 32768` full; coexist uses smaller budget |

**From an agent SSH session, always use WMI** — plain `Start-Process` dies when SSH disconnects:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\_tmp\wh3_muse_wmi_start.ps1
```

Full Muse needs ~17 GB VRAM. **Stop the learner** (`:8765`) before full start, or use coexist + slim. Verify health still answers after the SSH command returns.

## How the almanac is given

The model does **not** get a free-form wiki. It gets:

1. **System:** `PASS1_SYSTEM` from `re1_rl/phase1_route_council.py` (also re-exported via `_tmp/pl29_muse_box.py`).
2. **User:** tip-specific `/think` brief (live inv/box/HP, operator locks, JSON schema).
3. **Packet (JSON):** Phase-1 almanac — tip overlay + graph + loot + combat + frontier.

### Build recipe (canonical)

```text
build_phase1_context("cp05", enemies_killed=...)   # rooms, directed_edges,
                                                   # pickups_allowed, enemies,
                                                   # combat_strain, story_use_sites
+ tip overlay from live cell (pose, inventory, box, taken, done beats)
+ open_frontier(done, held) / remaining_phase1_beats(done)
+ strip taken pickups; drop blocked edges
+ operator_locks (tea one-way, shotgun return, tip cell, …)
→ compact JSON appended as PHASE1_PACKET in the prompt
```

Entry patterns:

| Piece | Where |
|-------|--------|
| `PASS1_SYSTEM` / `build_packet` | `_tmp/pl29_muse_box.py` |
| Tip overlays + call | `_tmp/plNN_muse_next.py` (e.g. `pl110_muse_next.py`, `pl134_muse_next.py`) |
| Validate | `validate_pass1_plan(plan, packet)` |
| Extract JSON | `scripts/probe_muse_phase1.py` (`_extract_json`, `_split_prompt`) |
| Kill ledger | `enemies_killed_from_sidecar(sidecar)` |
| Log of accepted plans | `data/planner_chunks/muse_chunk_log.md` |

### Tip inventory (C-RE1 cells)

Do **not** fingerprint-scan the raw `.pst` blob. Decode MainRAM:

```python
from _tmp._gen_planner_loyal_resources_md import pst_ram, slots_at
from re1_rl.memory_map import INVENTORY_BASE, INVENTORY_SLOTS, ITEM_BOX_BASE, PLAYER_HP, PS1_MAINRAM_BASE
ram = pst_ram(Path("states/planner_loyal/cells/plNN/cell.pst"))
# 8 inventory slots at INVENTORY_BASE; box from sidecar box_cache via ITEM_IDS
```

`docs/planner_loyal_resources.md` is regenerated the same way. Sidecar `box_cache` is authoritative for the shared 100/118 box when not in a box room.

## Invoke checklist

1. Confirm highest tip / `chunk_final` cell (e.g. `pl134`, beat `place_wind_crest`, room `11A`).
2. Decode inv/HP/box; set `done_beat_ids` and `known_taken_pickups` from prior Muse tips + chunk acquires.
3. If learner is up and you need full almanac: stop learner first (user must OK fleet tear-down).
4. Start Muse on WH3; wait for `HEALTH 200`.
5. Build prompt locally (`--build-only` optional); scp request JSON to WH3; `curl` `:8000`; scp raw back.
6. `validate_pass1_plan`; write `_tmp/plNN_muse_next_response.json`.
7. Summarize `end_anchor` + steps to the user. Append to `muse_chunk_log.md` only when the plan is **accepted/pinned**.

### Minimal call shape

```json
{
  "model": "muse-glimmer",
  "messages": [
    {"role": "system", "content": "<PASS1_SYSTEM + tip lock>"},
    {"role": "user", "content": "<USER brief + PHASE1_PACKET compact JSON>"}
  ],
  "max_tokens": 8192,
  "chat_template_kwargs": {"reasoning_strength": "low"}
}
```

SSH one-liner pattern: `_tmp/_wh3_muse_call_pl110.ps1` / Python `_call_via_wh3_ssh` in `pl110_muse_next.py`.

## Operator locks (fail closed)

Always restate in the user brief when still true:

- Author hops only from supplied `directed_edges`.
- Only move items that exist in tip inventory/box.
- On-path free ammo/heals: acquire remaining allowed pickups when entering a room (what fits).
- Tea one-way `103->104`: **locked until first traverse**; after unlock (done in place_wind walk), do **not** keep it in `BLOCKED_EDGES`.
- Shotgun return was `116->115->109` — never invent `116->106`.
- Combat kit = firearm + its ammo (loaded clip counts). Knife / boxed acid alone ≠ kit.
- Do not redo completed beats (crests already placed, jewels spent, etc.).

## Yawn kit (operator)

Before `attic_enter` / `yawn_1` from a thin tip (e.g. pl134 with only ~7 HG + ~4 SG loaded):

- **Not enough:** beretta/shotgun clips alone vs Yawn (~60 HG-rounds / ~4 acid GL in `combat_strain`).
- **Required staging:** withdraw boxed `acid_rounds`; acquire `212:bazooka_acid:1` (terrace via `203→211→212`); loot **209** (HG clip, red herb, lighter) and **20A** (`explosive_rounds` cabinet) via armor-key door `207→208`.
- Prefer `118` box over a long 1F walk to `100` when already near `10B`.
- `use_box` must emit `held_on_exit` (shield_key + bazooka + acid for the attic approach).


## Related

- `docs/planner_loyal_architecture.md` — Muse experiment section
- `docs/planner_loyal_cells.md` / `docs/planner_loyal_resources.md` — tip + kit
- `re1_rl/phase1_route_council.py` — contract + validation
- Skill `re1-fleet-git-ship` — if call scripts must land on WH3 via git

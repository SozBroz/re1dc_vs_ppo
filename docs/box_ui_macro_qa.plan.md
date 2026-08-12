# Box UI deposit / withdraw QA — Yawn firepower gate

**Status:** live QA on QS1 (2026-08-12): **D1 PASS**, **G0 ×5 PASS**. `MAGIC_BOX_RAM_WRITES_ENABLED` stays False.

**Fixture:** latest BizHawk QuickSave, standing in front of the 118 box:

`tools/BizHawk-2.11.1/PSX/State/Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State`  
(mtime 2026-08-12 13:38). Resolve at runtime with `re1_rl.bizhawk_paths.newest_quicksave()`.

**Live QS1 occupancy (do not assume knife@0):** box `empty@0`, `knife@1`, `bazooka_acid@2`. First empty dest = **0**. Inv: 8-pack, wind crest @7.

**ROM:** `D:\re1_rl\roms\Resident Evil - Director's Cut.cue` (SLUS-00551). Never glob `*.cue`.

**Mission:** prove the box macros implement the transfer contract below, then satisfy `cp89` `yawn_box_prep_118` (end state, not a unique sequence). Arrive at Yawn (`cp93` / room `210`) with the guns and ammo this loadout can carry — not a knife and a crest.

Live transfers are **D-pad macros only**. `MAGIC_BOX_RAM_WRITES_ENABLED` stays False.

### Live results (2026-08-12)

Harness: `scripts/_probe_box_ui_exhaustive.py`. Screenshots: `data/box_ui_qa/`.

| Gate | Result |
|------|--------|
| P0 crest deposit (D1) | **PASS** — crest → box[0], inv[7] empty, not `exchange_detected` |
| Illegal deposits D2–D4 | **PASS** — refuse before Cross, RAM unchanged |
| D5 / D5b / D6 | **PASS** — bazooka into first empty inv; knife withdraw legal; full pack no Cross |
| G0 ×1 and ×5 | **PASS** — `yawn_box_prep_capture_ready`; keys never in the box |
| C3 / C7 | **PASS** — Right from occupied cells stays in the inv grid; `down×3, right` lands on crest |
| C15 | **PASS** (negative) — occupied×occupied **exchanges**; macro must never do this |
| D7–D10, G1 | **SKIP** this run |
| Magic RAM writes | still **False** |

**Fixes that made D1/G0 true:**

1. `unexpected_keys_lost` — 118 crest bank is not a stolen-key fail.
2. **Do not `_home_inventory` on cold open.** Open already parks on inv slot 0. **Up from slot 0 hits EXIT**, then the assumed 0→7 path lands on CLIP and deposits bullets.

**Still true / still open:**

- Odd-column `_home_inventory` cannot reach slot 0 (no Left). Training must not call it from slot 0.
- Box-list `Up×15` from 0 still wraps to slot 33.
- Plan §6 items 2/5 (close box on `ok=False`+`ram_changed`; withdraw re-home) **not** implemented — G0 passed once cursors chained. Still the right fail-safe if a transfer false-fails again.
- Uncommitted: `item_box_ui_macro.py`, `env.py`, `tests/test_box_ui_transfer_contract.py`, harness, this plan.

---

## 0. Legal transfer contract (always)

The game’s item-box UI can **exchange** (Cross an occupied inv cell onto an occupied box cell). **That is always a bug in this project.** Training must never do it. QA fails the build if any slot other than the one source and the one dest changed.

A legal transfer moves **exactly one** item. Everything else stays the same.

| Direction | Source (policy picks) | Dest (automatic) | Precondition |
|-----------|----------------------|------------------|--------------|
| **Deposit** | One occupied inventory slot that is allowlisted for this room | **First empty modeled box slot** (`-Nothing-`) | Box has a modeled hole |
| **Withdraw** | Any occupied modeled box slot | **First empty inventory slot** | Inventory has **≥1 free slot** |

- Selecting Deposit / Withdraw does **not** pick a dest. The macro always parks the item in the first legal hole on the other side.
- Withdraw with a full pack is illegal. Mask it off; do not Cross onto an occupied inv cell.
- After a legal deposit: that inv slot is empty; one previously empty box slot holds the item; **all other inv and box bytes are identical**.
- After a legal withdraw: that box slot is empty; the first previously empty inv slot holds the item; **all other inv and box bytes are identical**.
- Merge-into-stack, overwrite, swap, and deep-list writes (slot ≥ 16) are all the same class of bug as exchange.

Policy still chooses **which** legal source (`select_slot_N` / `withdraw_box_N`). Dest is not a policy problem.

### This CP (`cp89` / room `118`) — deposit mask (current, not forever)

Illegal to deposit **right now**:

- Any non-knife weapon (beretta, shotgun, bazooka, …)
- Any ammo (handgun bullets, shotgun shells, acid/explosive/flame rounds, …)
- Either mansion key (`shield_key`, `armor_key`)

Legal to deposit **right now**: knife, ink ribbon, herbs/sprays, **wind crest**.

That allowlist may change later for this same checkpoint. Do not bake “crest is the only depositable item” into the macro. Bake “dest is first empty hole; never swap.”

### This CP — success is an end state

Leave `118` → `10B` with `lab_timer == 0` and:

1. `wind_crest` in the box, not on person
2. No guns or ammo in the box (knife may stay)
3. Mansion keys not in the box (they cannot be deposited under the current mask; if they appear there, that is the swap bug)

There is **no unique sequence**. Order can be withdraw-then-deposit or deposit-then-withdraw whenever the pack has a hole.

**Current typical 8-pack** (pistol ammo filling the last slot: beretta, bullets, shield_key, shotgun, acid_rounds, armor_key, shotgun_shells, wind_crest; **live QS1 box: empty@0, knife@1, bazooka@2**):

- The only legal deposit is the crest (no knife/heal on person).
- Withdraw is illegal until that deposit frees a slot.
- The **only** end inventory that can satisfy the CP is: **all guns + their ammo + both mansion keys on person**, crest (and knife) in the box.

**If this CP is reached with a hole** (e.g. no pistol ammo): withdraw is legal immediately. More sequences work. More end combinations can satisfy the CP (herb or knife in the box vs on person) as long as **every gun and ammo stack they actually have**, plus both mansion keys, ends on person, and the crest is banked. Missing pistol ammo is not a CP fail — it is a free slot. The macros must allow those packs; they must not assume the 8-pack story.

---

## 1. What the agent is doing (live, repeatable)

`data/logs/pking_top_right_memlog.jsonl` — same 4-step loop across many episodes (ep1, 3, 4, 5, 6, 8, 9, 11, …):

| Step | Action | After | Macro report |
|------|--------|-------|----------------|
| n | `deposit_slot_1` | 8-item pack including `wind_crest` @ slot 7 | `box_deposit_open` |
| n+1 | `select_slot_7` | 7 items, **crest gone**, shield_key still listed | `ok=false` `exchange_detected` **434 frames** |
| n+2 | `deposit_slot_0` | — | `box_withdraw_open` |
| n+3 | `withdraw_box_0` | crest **back on person** @ slot 2, **shield_key in box @2** | `key_item_in_box:shield_key@2` **328 frames** |

That is not “the policy is confused.” The policy picked the right deposit source. Step n+1 was a **legal one-item deposit** (crest → first empty box hole) that the macro then scored as a fail. Step n+3 was a **swap** (shield_key ↔ crest). Swap is never legal. User-visible: “depositing the wind crest, then placing the wind crest over a key.”

---

## 2. Root-cause ranking

### P0 — Successful 118 crest deposit is scored as a stolen key (code-proven)

`execute_box_deposit_ui` snapshots `keys_before` via `is_key_item_id`. Wind crest **is** a key item. `is_deposit_allowed_item` already special-cases `wind_crest_deposit_allowed` for room `118`, but the **post-Cross** check does not:

```1077:1094:re1_rl/item_box_ui_macro.py
    # Any key that left the person is an automatic fail (wrong-cursor deposit).
    keys_after = { ... is_key_item_id ... }
    if keys_before - keys_after:
        _finalize_transfer_failure(..., default_reason="key_item_deposited")
```

A correct crest bank makes `keys_before - keys_after == {0x29}`. `_finalize_transfer_failure` then sees RAM changed and **overwrites** the reason to `exchange_detected`. Memlog never shows `key_item_deposited`. Cursors are popped (`report.pop("inv_cursor")` / `box_cursor"`). Env `_apply_box_ui_cursors_from_report` zeros `_box_inv_cursor` / `_box_list_cursor` on `exchange_detected`.

The transfer already happened. Crest is in the first empty modeled box slot (typically **slot 2**: knife@0, bazooka@1). Software now believes both cursors are 0.

### P0 amplifier — next withdraw Crosses from a lie

Withdraw **does not re-home** inventory. It trusts `inv_cursor` (now 0) and only checks that the **RAM** dest slot is empty (`withdraw_dest_not_empty`). Empty slot 7 after the crest deposit passes that check even if the **red cursor** is still on slot 7, on the box list at dest 2, or on shield_key @2.

`inv_slot_drift` on deposit has the same hole: it checks the item is still in that RAM slot, not that the red cursor is on it.

Observed exchange `shield_key@2` ↔ `wind_crest@2` is Cross on **occupied inv slot 2** against **occupied box slot 2**. That is the game’s exchange, not a deposit onto `-Nothing-`.

### P1 — `_home_inventory` is vertical-only and unsafe from slot 0

Live C2/C9/D1: **Up from inv slot 0 hits EXIT** (header), not a no-op. Cold deposit must not home — open already parks on slot 0. `HOME_INVENTORY_TAPS` is Up×3 only; no Left. Odd columns still miss slot 0.

### P1 — env never trusts the cursor it just wrote

`env.py` always passes `trust_inv_cursor=False` into `execute_box_deposit_ui`, even after a successful transfer set `_box_inv_trusted_at_cursor = True`. Deposit therefore always navigates as if the cursor might be wrong, but still **starts from the tracked slot** (`deposit_inventory_nav_from` returns `cursor` either way — the flag is currently a no-op for the start slot).

### P2 — occupancy-agnostic Right (needs live proof, not assumed)

`dcc6861` made `box_inventory_nav_moves` ignore occupancy so slot 7 is reachable on a full pack (`down×3, right` from 0). Older live QA (2026-08-11) suggested Right from an **occupied** cell may no-op or **pane-switch** into the box list. If that is still true, every full-pack crest deposit Crosses on the wrong pane.

### P2 — box-list resume / wrap

Cross into the box list resumes at the last highlighted box slot. Software `box_cursor` after P0 is 0, so `_home_box_list` is skipped. UI is still on dest 2. Blind `Up×15` from slot 0 wraps to live slot 33 (already burned us with `chemical@33`). Home must be **exact taps from a known index**, or a verified RAM cursor, never a fixed 15.

### P3 — no red-cursor oracle

There is no trusted RAM byte for “which inv/box cell is highlighted.” Existing probes hunt; none is wired into the macro. Until one exists, **screenshot after every tap** is the oracle.

---

## 3. Invariants (fail the build if any break)

1. **One item, one hole.** After every `ok=True` transfer, exactly one source slot became empty and exactly one dest slot (the first empty on that side) became that item. Every other inv slot and every other live box slot is byte-identical. Any other delta is a swap/merge/deep-write — fail.
2. **Deposit dest** is `_first_empty_modeled_slot` only. Cross #2 on an occupied box cell is an exchange. Abort before Cross if dest RAM id ≠ 0.
3. **Withdraw dest** is `first_empty_inventory_slot` only. Illegal when the pack is full (no Cross at all). Cross #1 on an occupied inv cell is an exchange. Abort.
4. **Withdraw source** may be **any** occupied modeled box slot when a hole exists. Knife, crest, bazooka, ammo — all legal to take out. The CP end-state, not the mask, decides whether leaving them in the box is a success.
5. **118 deposit mask (current):** refuse before Cross: non-knife weapons, ammo, `shield_key`, `armor_key`. Allow: knife, ink, heals, wind crest. Do not treat this list as the CP success condition.
6. **Crest deposit at 118 is success.** `keys_before - keys_after == {wind_crest}` must not call `_finalize_transfer_failure`. Other keys leaving the person remain a fail.
7. **Cursors.** `cursor_out` only on `ok=True`. On any failure: do not assume 0. Either close the box UI or visually re-home both panes. Never issue a second transfer after a RAM-changing fail in the same open.
8. **No deep-box writes.** Dest `< 16`. Live 48-slot list must not change past index 15.
9. **CP end-state** (after close, before leave): crest in box not on person; no guns/ammo in box (knife OK); mansion keys not in box. For the current 8-pack that implies guns+ammo+both keys on person. For a 7-pack it may not require depositing first.

---

## 4. Layers (run in order; stop on red)

### L0 — Unit, no emulator (minutes)

| ID | Assert |
|----|--------|
| U1 | Room `118`, deposit crest @7, first empty box slot 2: `execute_box_deposit_ui` **success path** must treat `keys_before - keys_after == {0x29}` as OK. Extract the check into a pure helper and test it. |
| U2 | Same helper: `shield_key` leaving the person is still fail (`key_item_deposited`). |
| U3 | `_finalize_transfer_failure` + env cursor apply: `ok=False` + `ram_changed` zeros env cursors. After U1, a good crest deposit must **not** take this path. |
| U4 | `is_deposit_allowed_item(wind_crest, "118")` True; `"100"` False; `shield_key` False everywhere. |
| U5 | 118 cannot deposit ammo, non-knife guns, `shield_key`, `armor_key`. Can deposit crest @ slot 7 on a full 8-pack. |
| U6 | Withdraw: full pack → all `withdraw_box_N` masked off / `can_withdraw` false. After one hole, **every** occupied box slot is withdraw-legal (knife, bazooka, crest). Dest is always the first empty inv slot. |
| U7 | `plan_deposit` / `plan_withdraw` RAM identity: only source and first-empty dest change. |
| U8 | `box_inventory_nav_moves(0, 7, full_pack)` equals `slot_nav_moves(0, 7)` (`down, down, down, right`). Occupancy must not raise. |
| U9 | `_home_inventory` documented limitation: odd-column start does not reach slot 0. Test or replace. |
| U10 | 7-pack (no pistol ammo) + bazooka in box: withdraw is legal **before** any deposit; CP end-state still requires crest banked and guns/ammo out of the box. |

Existing `tests/test_yawn_box_prep_checkpoint.py` covers allowlist/mask/planner only. It never executes the post-Cross key check. That is why P0 shipped.

### L1 — Cursor physics on QS1 (screenshots, no policy)

New harness: `scripts/_probe_box_ui_exhaustive.py` (do not pile onto the 17 existing `_probe_box_*.py` scripts; they target QS0 and do not assert the Yawn pack).

**Every D-pad tap and every Cross:** screenshot → `data/box_ui_qa/<case>/<step>_<tag>.png`; dump `inv[0:8]`, `box[0:16]`, `box_live[0:48]`, `game_mode`, `game_state`. Human (or later a red-pixel crop) records **observed** inv slot and box slot vs **assumed**.

Open protocol: load QS1, Cross to open box, confirm `probe_box_ui_open`, screenshot “open_home”. Record whether open lands on inv slot 0.

| ID | Physics question | Pass |
|----|------------------|------|
| C1 | Open-box home cell | Inv cursor is slot 0 (or we measure the actual home and code to it) |
| C2 | Vertical wrap | From slot 0, `up` does not leave the grid / does not hit header. From slot 6/7, `down` does not wrap into the box list |
| C3 | Right from **occupied** slot 0 | Stays in inv grid on slot 1. Does **not** pane-switch |
| C4 | Right from **empty** slot 6 (after making a hole) vs occupied slot 6 | Same as C3 or document the difference; macro must match live |
| C5 | Left from slot 1 | Slot 0, not box list |
| C6 | Left from slot 0 / Right from slot 1 | **Does** jump to box list (known). Macro must never emit these |
| C7 | Path 0→7 on the **live 8-pack** (`down×3, right`) | Red cursor on wind crest. Screenshot proof |
| C8 | Path 7→0 | Inverse path lands on slot 0, still in inv grid |
| C9 | `_home_inventory` from every start slot 0..7 | Lands on slot 0. If not, replace home (e.g. close+reopen, or Left only from col 1 after vertical clamp) |
| C10 | Cross on occupied inv slot | Enters box list; resume index = last box highlight (measure) |
| C11 | Cross on **empty** inv slot | Enters box list for withdraw; resume index measured |
| C12 | Box list Down/Up from 0 | Slot 1 / no-op or wrap? Measure. Never assume wrap-to-33 on a single Up |
| C13 | `Up×N` from slot 0 for N in 1, 8, 15, 16 | Record landing slot. Confirm 15→33 still true so we never ship fixed-15 home |
| C14 | First empty dest with knife@0, bazooka@1 | Slot 2 is `-Nothing-`. Deposit Cross on 2 does not touch 0/1 |
| C15 | Cross on occupied box slot while inv highlight is occupied | Game **exchanges**. Confirm this is how shield_key@2 happened. Macro must refuse — swap is never a legal transfer |
| C16 | After a successful deposit, where is the red cursor? | Inv dest hole vs box dest vs slot 0. Macro `cursor_out` must match **observed**, not guessed |

### L2 — Single transfers (reload QS1 each case)

Typical QS1 pack (confirm on first open; do not hardcode if RAM differs):

- Inv: beretta, bullets, shield_key, shotgun, acid_rounds, armor_key, shotgun_shells, **wind_crest @7**
- Box: knife @0, `bazooka_acid` @1, empty @2+

| ID | Call | Pass |
|----|------|------|
| D1 | `execute_box_deposit_ui(7, room_id="118")` | `ok=True`, crest in box first empty, inv[7] empty, keys still on person, `cursor_out` matches C16, **not** `exchange_detected` |
| D2 | Deposit slot 2 (shield_key) | `ok=False` **before** any Cross (`key_item` / allowlist). RAM unchanged |
| D3 | Deposit slot 6 (shells) at 118 | Refused, RAM unchanged |
| D4 | Deposit slot 0 (beretta) at 118 | Refused, RAM unchanged |
| D5 | After D1, `execute_box_withdraw_ui(bazooka_slot)` | `ok=True`, bazooka in the **first empty inv slot**, that box slot empty, crest still in box, keys on person, no other slot changed |
| D5b | After D1, withdraw **knife** (box 0) | Also legal (`ok=True`, first empty inv). CP would then still need bazooka out before leave — that is an end-state problem, not a macro illegal |
| D6 | Withdraw any box slot with 8-pack (before D1) | `inventory_full`, RAM unchanged, no Cross |
| D7 | Withdraw onto a **non-empty** dest (force dest=2) | Abort; no exchange. Swap is never a fallback |
| D8 | Deposit crest when first empty is slot 0 (empty box) | Lands @0, not @2 |
| D9 | Two deposits in one session (knife then crest, or crest then herb if present) | Each dest is the first empty **at that moment**. Second uses `cursor_in` from first `cursor_out`. No re-zero |
| D10 | Deposit then withdraw then deposit (session) | Cursors chain; one-item identity each time; no pane-switch; no deep-box |

### L3 — Golden path for **this** QS1 loadout

QS1 is a full 8-pack with pistol ammo. For **this** inventory the only sequence that can reach a CP-satisfying end state is: deposit crest (only legal deposit) → withdraw every gun/ammo still in the box → close. That is a loadout fact, not a macro rule.

**G0** — one session, no reload between steps:

1. Open box from QS1.
2. Deposit wind crest (slot 7) → first empty box slot. Identity check.
3. Withdraw bazooka → first empty inv slot. Identity check.
4. If other guns/ammo remain in the box, withdraw them (each into the first empty inv slot at that moment) until `yawn_box_weapon_ammo_clear`.
5. Close box.
6. Assert `yawn_box_prep_capture_ready`: crest in box, not on person, no `key_item_in_box`, no guns/ammo in box.
7. On-person for this loadout: shotgun + shells + acid + bazooka + shield_key + armor_key (+ beretta + bullets). Knife may remain in the box.

**Pass:** G0 × **5 consecutive reloads**, screenshots archived, zero exchanges, zero cursor pops, zero pollution.

**G1** — same as G0 but **close and reopen** between deposit and withdraw (session cursor reset). Proves cold-open withdraw still finds bazooka.

**G2** — G0 then leave to `10B` is **out of scope for the macro harness** (needs walking). After macros are green, a single `play_human` / env episode from the cp88 cell (or QS1) must satisfy `yawn_box_prep_exit`.

**G3** (unit or a second savestate if we have a 7-pack): hole on arrival → withdraw bazooka **first** is legal; crest still on person until a later deposit. Both orders must pass identity checks. Do not skip this just because QS1 is full.

### L4 — Failure recovery (must not poison the next action)

| ID | Inject | Pass |
|----|--------|------|
| F1 | After a **forced** failed Cross (e.g. Triangle mid-nav), next transfer does not assume cursor 0 | Either box UI closed, or both panes re-homed with screenshot proof |
| F2 | Reproduce pre-fix P0 (crest deposit scored fail) | Post-fix: does not happen. Regression test stays |
| F3 | `exchange_detected` if it still fires | Env must not issue another transfer in the same open; close + fail episode is acceptable; silent retry is not |
| F4 | Pollution `key_item_in_box` | Episode fail, no further withdraw/deposit |

### L5 — Env / mask integration (can be unit + one live smoke)

| ID | Assert |
|----|--------|
| E1 | 118 deposit phase: crest (and knife/heals if present) on; ammo, non-knife guns, both mansion keys **off** |
| E2 | Full pack: Withdraw action off. After one hole: **every** occupied box slot’s `withdraw_box_N` on (not only guns) |
| E3 | `trust_inv_cursor` policy: either start trusting `cursor_out` after `ok=True`, or always visually home. Pick one; today’s mix is how we lose the cursor |
| E4 | Close resets both cursors to 0 **and** the next open is measured (C1) |

---

## 5. Harness requirements

Single script, QS1, kill EmuHawk on exit.

- Launch via `emuhawk_argv(port=...)` + `assert_rom_present()`.
- `load_savestate` QS1 at the start of **every** case except G0/G1/D9/D10 (those are session tests).
- JSONL report: `data/box_ui_qa/report.jsonl` with `id`, `ok`, `assumed_inv`, `assumed_box`, `inv`, `box_16`, `box_live_delta`, `reason`, `screenshot`.
- Fail fast on RAM invariant break (key in box, deep write, exchange).
- Do not use the PPO env for L1–L3. Call `execute_box_deposit_ui` / `execute_box_withdraw_ui` directly so policy noise cannot hide macro bugs.
- After P0+P1 code fixes, re-run the **whole** matrix. A green D1 on a dirty C7 is not a pass.

Existing probes to steal from, not to run as the suite: `_probe_box_cursor_map.py` (screenshot loop), `_probe_box_deposit_first_empty.py`, `_probe_box_crowded_inventory.py`, `_probe_box_list_up_wrap.py`.

---

## 6. Code fix order (only after U1 is written, before claiming L2 green)

1. **P0:** Exclude the intended deposit id from the stolen-key check when `is_deposit_allowed_item(item_id, room_id)` is True (118 crest). Keep the check for every other key. Unit test U1/U2 first.
2. **Cursor honesty:** On `ok=False` with `ram_changed`, **close the box** (or hard-fail the episode) instead of zeroing cursors and continuing. Zeroing is what turns a crest bank into a key exchange.
3. **Pre-Cross cursor proof:** Before deposit Cross #1 and withdraw Cross #1, require the highlighted cell to match the target. Until a RAM cursor exists: optional screenshot in the probe; in training, at least re-read and refuse if we cannot home. Hunt a cursor byte in parallel (C-layer), do not block G0 on it if screenshots already prove the D-pad path.
4. **Home that actually homes:** Fix `_home_inventory` so odd columns reach slot 0 without pane-switch (Left only from col 1 after vertical clamp is the obvious candidate — **prove with C9** before shipping).
5. **Withdraw home after any prior transfer or any failure.** Do not navigate from a guessed 0.
6. **Right-from-occupied (C3/C7).** If live Right pane-switches, revert occupancy-agnostic nav and find another authentic-UI way to highlight slot 7 (close/reopen, Triangle, different tap counts). **Never re-enable `MAGIC_BOX_RAM_WRITES_ENABLED`.** That path is legacy; live transfers are D-pad macros only.

Do not restart the fleet to “see if P0 alone is enough.” Run G0×5.

---

## 7. No magic RAM writes

`MAGIC_BOX_RAM_WRITES_ENABLED` stays **False**. `apply_deposit` / `apply_withdraw` are retired. Do not flip the flag, do not add a 118-only write_ram path, do not seed probe inventories with `write_ram`. QA and training both go through `execute_box_deposit_ui` / `execute_box_withdraw_ui`.

If C3/C7 or G0 fail, **fix the macros**. Capture `cp89` only from a state that already satisfies `yawn_box_prep_capture_ready`. There is no `states/yawn_rails/cells/cp89/` until that is true.

---

## 8. Release gates (all required)

- [x] Fleet remains down (learner HTTP 8765 down; no `distributed_train` on pking / WH1 / WH2).
- [x] U1–U10 green in `pytest`.
- [x] L1 C1–C16 screenshots in `data/box_ui_qa/` (C2/C9: Up from slot 0 hits EXIT).
- [ ] D1–D10 green on QS1 — **D1–D6 PASS; D7–D10 SKIP this run.**
- [x] **G0 × 5** green on QS1: one-item identity each transfer; crest banked; bazooka on person; mansion keys never in the box.
- [x] D1/G0 not `exchange_detected` / `key_item_in_box` after the P0 + no-cold-home fixes.
- [x] `MAGIC_BOX_RAM_WRITES_ENABLED` still False.
- [ ] Commit + git ship, pull workhorses, restart fleet, pin `88-100` still local.

---

## 9. Out of scope

- Teaching the policy which **source** slot to pick (dest is automatic). On QS1 it already picks crest @7.
- Combat macros, Yawn HP, moon crest.
- Room 100 boss-bank macro except as a non-regression if we touch shared helpers.
- Capturing cp89 before G0 is green.
- Restarting the fleet “to collect more box data.”
- Re-enabling magic box RAM writes.

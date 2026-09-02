# Human demonstrations → behavioural cloning (planner-loyal)

Shipped 2026-09-02 (`e98bfcf`, `fe687c7`). Lets a human beat a planner-loyal
leg a handful of times and have PPO imitate those decisions while it keeps
training. First target: **pl79 → pl80** (Armor Room 205, west statue onto the
far vent), which the fleet had not minted in 48 h.

Purity note: this is learning from demonstrations, not scripted actuation.
The policy still chooses every action at runtime; the demos only add a
supervised pull on the policy head (DAPG-style auxiliary term).

## 1. Record demos (pking)

```powershell
venv\Scripts\python.exe scripts\record_planner_demo.py                 # pl79, keyboard + pad
venv\Scripts\python.exe scripts\record_planner_demo.py --speed 150     # faster emu
venv\Scripts\python.exe scripts\record_planner_demo.py --keep-failures # also save fails
venv\Scripts\python.exe scripts\record_planner_demo.py --start-index 83 # another cell
```

What it does:

- Launches an isolated EmuHawk on port **5801** (not a fleet worker port) and a
  real `RE1Env` with the planner-loyal curriculum, env knobs mirrored from
  `fleet/local/planner_loyal.env.cmd`, and a private reset pin
  (`data/logs/_demo_reset_pin_5801.env`, `RE1_PLANNER_RESET_PIN_INDEX=<start>`).
  Learner sync / cell capture are forced off; a recorder never mints cells.
- Every `frame_skip` (8-frame) batch your held buttons are mapped to **one
  discrete PPO action** (`re1_rl/demo_record.py::buttons_to_action`) and sent
  through `env.step`. The observation Dict returned by the env and the legal
  action mask are stored with that action. Steps where only `noop` is legal
  (async cutscene/door skip owns the input) are not stored as decisions.
- Terminals for this recorder: first planner-loyal **step** success for the
  pinned leg (reward log shows `checkpoint_success` / `planner_step_success` —
  that is the pay, not a Gym terminal; the env only ends when the whole chunk
  finishes). Fail terminals still apply (`planner_divert`,
  `armor_inplace_statue_push`, `armor_gas`, 12 min wall). After each episode it
  prints a tally and auto-resets to the pinned cell.
- Successful episodes are written to
  `data/demos/planner_loyal/plNN_<stamp>_ok.npz`; failures are discarded
  unless `--keep-failures` (then `_fail.npz`, ignored by the learner unless
  `RE1_BC_INCLUDE_FAILS=1`).

Controls:

| Input | Keyboard | Gamepad (focus EmuHawk window) |
|-------|----------|--------------------------------|
| Move / turn | W A S D (or arrows) | left stick / d-pad |
| Run | Shift + W | Square + up |
| Interact / push confirm | Z or E | Cross |
| Aim / fire | R / F | R1 / R2 |
| Stand still (noop; clears sticky) | Space | Circle |
| Quit (discards current episode) | Esc or Q | — |

Mapping details worth knowing when you play: the action space cannot express
"walk + turn" in one step, so holding W+A becomes `forward` (latch up) then
`turn_left` with the forward latch kept — the same two-step the policy must
use. Releasing the turn while still walking/running maps to `forward` /
`run_forward` (clears the turn latch, keeps up/run). Flipping A↔D while
holding W is a single `turn_left`/`turn_right` (opposite side cleared).
Holding Cross sends `interact` (18-frame hold) every step. Pushing a statue
is just repeated `forward`/`run_forward` into it (`re1_rl/pushable.py`,
8-frame hold in 205).

Aim for ~10 successes. Do not worry about speed; wasted steps are fine, but
avoid actions you would not want the policy to copy (interact spam, pacing).

## 2. Ship demos to the learner

```powershell
powershell -File fleet\local\ship_demos.ps1
```

Commits `data/demos/planner_loyal/*.npz`, pushes, `git pull` on WH3, and
lists the demo dir on WH3. The learner rescans the directory every
`RE1_BC_RELOAD_EVERY` train calls and prints
`[demo_bc] loaded N decisions from K demo(s)`; **no restart needed**.

Cadence gotcha: the WH3 planner-loyal learner runs **one `train()` per sync
interval (~6 min)**, so `RE1_BC_RELOAD_EVERY=20` (the code default) meant a
~2 h rescan. The WH3 launcher now sets `RE1_BC_RELOAD_EVERY=1` (a rescan is a
cheap directory stat); a learner started before that change keeps the 20-call
cadence until its next restart. Each `train()` runs ~4 epochs × ~30
minibatches, and every minibatch adds a `RE1_BC_BATCH` (128) demo batch, so
one update is roughly 8–10 passes over a ~1.8k-decision demo set at
`RE1_BC_COEF` — already a strong pull. To check it engaged on WH3:

```powershell
ssh sshuser@192.168.0.229 "cd /d C:\Users\sshuser\re1_rl && findstr /C:\"[demo_bc]\" data\logs\learner_wh3_planner_loyal.log"
```

and watch the `train/bc_loss` / `train/bc_acc` / `train/bc_n` keys in
`logs/training_metrics_planner_loyal_shield_key.jsonl` (one record per update).

## 3. How training uses them

`re1_rl/demo_bc.py::DemoBCAux`, wired into
`CombatEfficientPPO.train()` (`re1_rl/combat_ppo.py`):

- Per PPO minibatch, sample `RE1_BC_BATCH` (128) demo decisions and add
  `coef · mean(−log π(a_demo | s_demo))` with the recorded action mask
  applied, to the PPO loss. ModDrop is disabled for the demo forward.
- Coefficient `RE1_BC_COEF` (0.5), optional per-update decay
  `RE1_BC_COEF_DECAY` (1.0) down to `RE1_BC_COEF_MIN` (0.05).
- Logged: `train/bc_loss`, `train/bc_acc` (greedy = demo action),
  `train/bc_n` (decisions loaded), `train/bc_coef`.
- Files whose `n_actions` or `obs_schema_version` differ from the learner are
  skipped with a printed reason. Frames are stored HWC (env-native) and
  transposed to the policy's CHW layout on load.
- Lazy init from env vars on the first `train()`; never pickled into the
  checkpoint (`_excluded_save_params`). Learner launcher
  `fleet/local/run_distributed_learner_wh3_planner_loyal_stack.cmd` sets
  `RE1_BC_DEMO_DIR=data\demos\planner_loyal` and `RE1_BC_COEF=0.5`.

Knobs (learner env):

| Var | Default | Meaning |
|-----|---------|---------|
| `RE1_BC_DEMO_DIR` | unset (off) | demo directory, relative to repo root |
| `RE1_BC_COEF` | 0.5 | BC weight in the PPO loss |
| `RE1_BC_COEF_DECAY` | 1.0 | multiply per `train()` call |
| `RE1_BC_COEF_MIN` | 0.05 | floor after decay |
| `RE1_BC_BATCH` | 128 | demo decisions per PPO minibatch |
| `RE1_BC_RELOAD_EVERY` | 20 | rescan interval (train calls) |
| `RE1_BC_INCLUDE_FAILS` | 0 | 1 = also learn from `_fail.npz` |

## 4. Demo file format (`re1_rl/demo_record.py`)

```
obs__<key>    (T, ...)   env-native dtype/shape for every observation key
action        (T,)       int64 discrete action
action_mask   (T, 45)    bool legal actions at that decision
reward        (T,)       float32 reward received for that decision
meta          0-d str    JSON: schema, obs_schema_version, n_actions, start_cell,
                         objective, success, reason, frame_skip, commit, ...
```

## 5. Gotchas

- **Observation schema changes invalidate demos.** Any change to the obs Dict
  or `ACTION_NAMES` must bump `OBS_SCHEMA_VERSION` (currently **3** after the
  `pushables` slots); old demos are then skipped and must be re-recorded.
- Record with the same env config the fleet trains with (the script mirrors
  `planner_loyal.env.cmd`; `RE1_CAMERA_WHITEN=0`, `RE1_LAYERED_GEOMETRY=0`).
  Speed (`--speed`) does not affect recorded actions or observations.
- Only the WH3 learner reads demos; workers are unchanged.
- Watch `train/bc_acc`: if it saturates near 1.0 on the demo states while the
  fleet still fails the leg, the gap is distribution shift / exploration, not
  representational capacity. If it stays low, the obs do not disambiguate the
  human's choices (see `docs/nn_architecture_review/11_pl80_armor_statue_nn_review.md`).
- The recorder's EmuHawk is killed on Esc / Ctrl+C. If the process is force
  killed, the EmuHawk with `--socket_port=5801` is orphaned; kill it by hand.

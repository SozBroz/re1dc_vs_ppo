# 11 — Is pl80 (Armor Room west statue) an NN limitation?

Review date 2026-09-02, after ~48 h of fleet time on pl79 → pl80 with no mint.
Scope question: should the policy network change for what we are asking it to
do (planner-loyal rails through the mansion, puzzles included), and is the
Armor Room stall caused by the network?

Short answer: **no, not capacity.** The stall is an *observability* gap (the
policy cannot read the statue or vent geometry except from 63×84 grey pixels
and a phase-switched compass) stacked on a hard exploration problem (a ~60
step precise sequence with four −4 terminals around it). One structured
observation is worth adding; the rest of the architecture is adequate for
scope. Behavioural cloning (`docs/human_demos_bc.md`) is the right immediate
lever and also the cheapest capacity test we have.

## 1. What the fleet policy is today

Source: `re1_rl/combat_efficient_extractor.py`, `re1_rl/policy_config.py`,
`re1_rl/async_fleet.py`, `re1_rl/distributed/learner_train.py`.

| Piece | Fleet value |
|-------|-------------|
| Frame input | 4 × 63×84 grayscale, stride 8 emulated frames (stack spans ~0.5 s) |
| Frame encoder | NatureCNN: 8/4 → 4/2 → 3/1 convs, flatten 1792 → 512 |
| Structured towers (planner-loyal) | proprio 64, spatial 192, inventory+box+keys 160, visited 64, goal 256 (+ zero-init `planner_steps` residual), combat 128, named_state 96 |
| Fusion | concat 1472 → LayerNorm → Linear 1024 → ReLU; no attention/gating (FiLM off) |
| Heads | tanh MLP [512,512] pi and vf; Discrete(45) categorical with action masking |
| Memory | none (feedforward); `history`/`world_state` omitted under planner-loyal |
| Params | ~3.4 M extractor (cap 8 M) |
| PPO | lr 1e-4, n_steps 1125, batch 4096 (WH3), 4 epochs, γ≈0.9963 (25 s half-life), ent 0.01, clip 0.2, **gae_lambda 1.0 with Monte-Carlo episode returns** |

Aux heads (combat outcome, world events) at coef 0.02. ModDrop off.

## 2. What the policy can see in room 205

`re1_rl/planner_loyal.py::encode_planner_loyal_goal` +
`re1_rl/armor_room_puzzle.py::armor_statue_goal_target`.

- **Jill pose**: `proprio` has `x_local = x mod 4096 / 4096`, same for z,
  facing sin/cos, cam_id embedding, 4-step anim history. Room 205 spans X
  ≈ 4800..14300, so `x_local` wraps twice across the room; absolute position
  is only recoverable together with cam_id / pixels.
- **Compass** (`goal[5:10]`): Δx, Δz, dist, bearing to ONE target, /4096.
  In 205 the target is a **fixed human waypoint chosen by phase**:
  east approach → east push endpoint (pl79) → west approach (8704, 8708) →
  west push endpoint (8539, 8008) → once the statue Z is inside the seat band,
  lateral approach (9617, 7179) → lateral push endpoint (5717, 7136) → button.
  Switching happens when Jill is within 384 units of the approach point or
  `game_state == PUSH_GAME_STATE`.
- **Not observed anywhere**: `armor_west_statue_x/z`, `armor_east_statue_x/z`
  (they are in `state` for reward/mint gating only), the vent targets, the
  Jill→statue offset, the statue→vent offset, or an explicit "pushing" bit.
- **Frames**: fixed-camera 63×84 grey; the statue is a large object and is
  visible, but 50-unit shove increments and the 350×250 seat box are a couple
  of pixels at this resolution.

So the task the network is actually given is: from pixels + a compass that
jumps between waypoints, infer where the statue is relative to you, line up
on the correct side, push ~7 steps north, walk around, push ~40 steps west,
and stop inside a box it cannot see. It is learnable (humans do it from the
same screen), but it is the hardest inference the policy has been asked to
make so far, and nothing in the guidebook-style obs helps.

## 3. Why it has not been learned (ranked)

1. **Exploration with terminal traps.** From the pl79 spawn Jill is jammed
   against the seated east statue; `forward` re-shoves it (−4 terminal).
   Leaving the room is `planner_divert` (−4). Pressing the button early is
   `armor_gas` (−4). The successful sequence is ~250 env steps long and the
   first positive drip (`armor_statue_progress`) only fires once the west
   statue actually moves, i.e. after ~100 steps of correct navigation paid
   only by the ±0.5 approach potential. PPO with 0.01 entropy on-policy is
   very unlikely to stumble into that.
2. **Observability** (section 2). Even once the drips start, "am I aligned
   with the vent" is not in the structured obs, so the value function has
   to learn it from pixels under heavy MC-return variance.
3. **MC returns (gae_lambda = 1).** Chosen for sparse checkpoint rewards
   (review 06). For a 250-step leg bracketed by ±4 terminals it makes the
   advantage estimate noisy exactly where fine control matters. Not the root
   cause, but it slows the fix once shaping starts paying.
4. **Not** parameter count, not fusion, not lack of recurrence. Nothing in
   the leg requires memory beyond the 4-frame stack plus the phase compass,
   and the policy head has ample capacity for a 45-way decision on 1024
   features.

## 4. Recommendations for scope

### Do now (already shipped)
- **Behavioural cloning from ~10 human runs** (`docs/human_demos_bc.md`).
  This attacks (1) directly and is also the diagnostic for (2): if
  `train/bc_acc` climbs to ~1.0 on demo states the network can represent the
  policy from the current obs; if it plateaus well below, the obs do not
  disambiguate the human's choices and the change below becomes mandatory.
- **`pushables` obs (schema v3, 2026-09-02):** up to 2 slots with Jill→object
  compass + object→crumb-target remaining `(dx,dz,dist)` using the same vent
  / dining targets the ±0.5 shove crumb pays on, plus `active` / `seated`
  bits. Zero-init residual into the goal tower (`combat_efficient_extractor`).
  Invalidates prior demos; re-record after the fleet is on schema 3.

### Add next (guidebook-compatible observation, small)
- Room-AABB-normalised Jill coordinates (replace wrapped `x_local`/`z_local`)
  so position is unambiguous inside long rooms. Separate from pushables.

### Consider, lower priority
- **GAE λ ≈ 0.95 for shaped legs.** Keep MC for sparse rails if wanted, but
  the armor/dining statue legs and any drip-shaped puzzle benefit from
  variance reduction. A per-cell switch is cheap; do not flip it fleet-wide
  mid-run.
- **CoordConv channels on the frame encoder** (two constant x/y planes):
  ~zero params, helps pixel-position tasks like docking. Only if the
  structured slots above are rejected.
- **Recurrence (GRU over fused features)**: not justified by scope. The
  planner queue, `visited`, and cutscene/milestone ledgers already carry the
  long-horizon state the rails need; an RNN would slow the fleet's batched
  inference and make BC noisier. Revisit only if a leg demonstrably needs
  hidden state that no ledger provides.
- **Larger CNN / higher-res frames**: not indicated. Structured geometry is
  far cheaper than asking pixels to resolve 50-unit shoves.

## 5. Verdict

pl80 is a limitation of what we *show* the network and of exploration, not
of the network. Keep the architecture; add demos now, add the movable-object
observation if BC accuracy says the obs are insufficient, and reconsider MC
returns for shaped puzzle legs.

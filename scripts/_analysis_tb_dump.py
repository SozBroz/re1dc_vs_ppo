"""Dump key scalars from PPO_7 / PPO_8 TensorBoard event files."""
from pathlib import Path

from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

ROOT = Path(__file__).resolve().parents[1] / "logs" / "tb"
TAGS = [
    "rollout/ep_rew_mean", "rollout/ep_len_mean",
    "train/approx_kl", "train/entropy_loss", "train/explained_variance",
    "train/value_loss", "re1/best_waypoint", "re1/gallery_hits",
]

for run in ("PPO_7", "PPO_8"):
    d = ROOT / run
    if not d.is_dir():
        continue
    acc = EventAccumulator(str(d))
    acc.Reload()
    print(f"=== {run} ===")
    avail = set(acc.Tags().get("scalars", []))
    for tag in TAGS:
        if tag not in avail:
            print(f"  {tag}: (absent)")
            continue
        ev = acc.Scalars(tag)
        pts = [ev[0], ev[len(ev) // 4], ev[len(ev) // 2], ev[3 * len(ev) // 4], ev[-1]]
        s = ", ".join(f"{p.step}: {p.value:.4g}" for p in pts)
        print(f"  {tag}: {s}")
print("TB_DUMP_DONE")

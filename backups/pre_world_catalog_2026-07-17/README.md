# Pre–world-catalog backup (2026-07-17)

Snapshot before Static World Map Obs / WorldAware extractor overhaul.

- Git branch: `backup/pre-world-catalog-2026-07-17`
- Latest training checkpoint: `ppo_re1_126602090_steps.zip` (~126.6M steps, reward_tune_1040k)
- Source tree: committed on that branch (fleet + re1_rl + tests as of snapshot)

## Restore

```powershell
cd D:\re1_rl
git fetch origin
git checkout backup/pre-world-catalog-2026-07-17
# weights:
Copy-Item backups\pre_world_catalog_2026-07-17\ppo_re1_126602090_steps.zip data\checkpoints\reward_tune_1040k\ -Force
```

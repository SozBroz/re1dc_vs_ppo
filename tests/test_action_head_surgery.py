"""Unit tests for action-head logit drain."""

from __future__ import annotations

import torch
from torch import nn

from re1_rl.action_head_surgery import drain_action_logits
from re1_rl.env import ACTION_NAMES


class _Policy(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.action_net = nn.Linear(8, len(ACTION_NAMES))


class _Model:
    def __init__(self) -> None:
        self.policy = _Policy()


def test_drain_equip_aligns_below_movement_mean() -> None:
    model = _Model()
    with torch.no_grad():
        model.policy.action_net.bias.fill_(0.0)
        for name in ("forward", "back", "turn_left", "turn_right", "run_forward"):
            model.policy.action_net.bias[ACTION_NAMES.index(name)] = 2.0
        model.policy.action_net.bias[ACTION_NAMES.index("equip")] = 9.0
        model.policy.action_net.weight.fill_(1.0)

    report = drain_action_logits(
        model, actions=("equip",), factor=100.0, weight_scale=0.25
    )
    eq = ACTION_NAMES.index("equip")
    after = float(model.policy.action_net.bias[eq])
    assert after == report["actions"]["equip"]["bias_after"]
    # movement mean 2.0 - log(100) ≈ -2.605
    assert after < 0.0
    assert abs(after - (2.0 - torch.log(torch.tensor(100.0)).item())) < 1e-5
    wn = float(model.policy.action_net.weight[eq].norm())
    assert abs(wn - (8.0**0.5) * 0.25) < 1e-5

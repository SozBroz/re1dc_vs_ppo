from unittest.mock import MagicMock

from re1_rl.action_mask import action_mask
from re1_rl.grab_escape import (
    GRAB_ESCAPE_CAPTURED_BEST,
    GRAB_ESCAPE_FRAMES,
    execute_grab_escape_noop,
    grab_bite_transition,
    grab_escape_frame_buttons,
)


def _state(**overrides):
    state = {
        "hp": 96,
        "in_control": True,
        "player_anim": 0,
        "player_aux": 0,
        "x": 100,
        "z": 200,
    }
    state.update(overrides)
    return state


def test_grab_bite_transition_matches_observed_signature() -> None:
    assert grab_bite_transition(_state(), _state(hp=84))
    assert not grab_bite_transition(_state(), _state(hp=83))
    assert not grab_bite_transition(_state(), _state(hp=84, x=101))
    assert not grab_bite_transition(_state(), _state(hp=84, player_anim=1))
    assert not grab_bite_transition(_state(), _state(hp=84, in_control=False))


def test_grab_escape_replays_best_captured_human_window() -> None:
    frames = grab_escape_frame_buttons()
    assert len(frames) == GRAB_ESCAPE_FRAMES == 8
    assert frames == list(GRAB_ESCAPE_CAPTURED_BEST)


def test_grab_escape_mask_allows_only_noop() -> None:
    mask = action_mask(46, None, grab_escape_pending=True)
    assert mask.tolist() == [True] + [False] * 45


def test_noop_executes_shared_eight_frame_mash() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (123, False)
    died, frames = execute_grab_escape_noop(bridge)
    assert not died
    assert frames == 8
    assert bridge.step.call_args.kwargs["frame_buttons"] == (
        grab_escape_frame_buttons()
    )

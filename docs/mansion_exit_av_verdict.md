# RE1-C vs BizHawk — mansion-exit footage A/V verdict

## Question

Can we collect mansion-exit footage (video + audio streams) on RE1-C / C-RE1,
or should we switch back to BizHawk for that work?

## Verdict

**Step 1 (joypad tape) works on RE1-C today. Video from SHM screenshots also
works. YouTube-grade audio+mux is not ready on the current recomp tooling —
not because of a second monitor, but because our ffmpeg build has no WASAPI
and the plugin has no A/V dump command.**

You do **not** have to throw away cells if we stay on RE1-C for replay/film.
`.pst` stays valid on recomp forever. What BizHawk cannot load is only the
savestate file format — weights + tapes still bridge the gap.

## Live probe notes (2026-09-11)

| Surface | Result |
|---------|--------|
| Joypad tape | **PASS** |
| Video @ **60 fps** guest cadence (`scripts/record_recomp_letsplay.py`) | **PASS** — 180 frames / 3.00 s, 960×720 nearest upscale |
| Policy-step grabs (old probe) | Rejected — ~7.5 fps, not let's-play |
| Audio via bundled ffmpeg WASAPI | **FAIL** — demuxer missing |
| Audio via recomp debug taps | **FAIL** on fleet exe — `PSX_DEBUG_TOOLS=OFF` |
| Audio via `pyaudiowpatch` loopback subprocess | **Armed** — records named device loopback (e.g. VG248); game must play to that Windows default output |

Do **not** use console Beep / loud test tones while validating loopback.

Authentic path: one guest VBlank per frame → ffmpeg `-r 60`, plus loopback of the dedicated monitor's playback device (quiet ambient / game audio only).


## Evidence (already in repo)

| Surface | RE1-C (recomp) | BizHawk |
|---------|----------------|---------|
| Fleet mint (`cell.pst`) | Live (80 workers) | Legacy `cell.State` backup only |
| Screenshots / frame obs | Yes (SHM 320×240) | Yes |
| Core A/V dump (`--dump-type=ffmpeg`) | **No** on plugin path | Yes (intended path) |
| Audio mix on headless RL | Off (`RE1_RL_NO_AUDIO_MIX=1`) | Core dump or WASAPI loopback |
| `capture_session.py` pickups | Not ported | Canonical |
| `record_planner_demo.py` | BizHawk default; some `record_pl*.py` on recomp for BC only | Canonical BC |
| Footage scripts | n/a | `replay_footage.py` / `replay_leg.py` (flags partially drifted — restore before batch) |

Canonical writeups:

- [`docs/turbo_runtime/08q_capture_surface.md`](turbo_runtime/08q_capture_surface.md) — W19: screenshots PASS, A/V dump NOT on plugin path
- [`docs/turbo_runtime/06b_parity_capture_footage_roundtrip.md`](turbo_runtime/06b_parity_capture_footage_roundtrip.md) — film on BizHawk via tape (G3) or live policy (F1)

Runtime taps exist (`audio_trace_dump_wav` on SPU/CD-XA, `sw_render_display`) but
are debug-ring only — no SHM audio command, no ffmpeg pipe, no plugin A/V writer.

## Practical path for mansion-exit footage

1. Enable room segments on recomp workers (`RE1_PLANNER_ROOM_SEGMENTS=1`).
2. Keep best `S_room` in `data/planner_loyal_room_segment_best.json`.
3. Export joypad tapes (or run F1 live policy) from champion segment episodes.
4. Replay/film on BizHawk twin → `replay_av.mp4` under `data/footage/`.

Do **not** wait on a recomp-native A/V dump for this milestone.

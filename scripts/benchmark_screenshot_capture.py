"""Benchmark BizHawk screenshot capture paths (live EmuHawk required).

Compares:
  file_png     — production path: client.screenshot(path) + cv2.imread (disk)
  mmf_mmap     — comm.mmfScreenshot() + Python mmap tag read (no frame file)
  mmf_b64_json — MMF capture + Lua mmfRead + base64 in JSON (no frame file)

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\benchmark_screenshot_capture.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\benchmark_screenshot_capture.py --iters 100 --port 5791
"""

from __future__ import annotations

import argparse
import base64
import mmap
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"


def _resize_gray(rgb: np.ndarray) -> np.ndarray:
    from re1_rl.env import _resize_frame

    return _resize_frame(rgb)[..., 0]


def _decode_png_bytes(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("cv2.imdecode failed on PNG bytes")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _bench(fn: Callable[[], np.ndarray], *, iters: int, warmup: int) -> tuple[list[float], np.ndarray]:
    last = np.zeros((240, 320, 3), dtype=np.uint8)
    for _ in range(warmup):
        last = fn()
    times_ms: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        last = fn()
        times_ms.append((time.perf_counter() - t0) * 1000.0)
    return times_ms, last


def _summarize(name: str, times_ms: list[float]) -> str:
    p95 = sorted(times_ms)[max(0, int(len(times_ms) * 0.95) - 1)]
    return (
        f"{name:10s}  median={statistics.median(times_ms):6.2f} ms  "
        f"mean={statistics.mean(times_ms):6.2f} ms  p95={p95:6.2f} ms  "
        f"max={max(times_ms):6.2f} ms"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark screenshot capture paths")
    ap.add_argument("--port", type=int, default=5791)
    ap.add_argument("--iters", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--headless", action="store_true", help="Launch with --gdi --chromeless")
    ap.add_argument(
        "--include-b64",
        action="store_true",
        help="Also benchmark MMF->Lua->base64->JSON path (experimental)",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient

    port = int(args.port)
    shot_path = str(ROOT / "data" / f"_bench_frame_{port}.png")
    mmf_name = f"re1_screenshot_{port}"
    bridge = BizHawkClient(port=port, timeout=300.0, screenshot_path=shot_path)
    bridge.start_server()

    emuhawk_cmd = [
        str(EMUHAWK),
        str(ROM),
        f"--lua={LUA}",
        "--socket_ip=127.0.0.1",
        f"--socket_port={port}",
    ]
    if args.headless:
        emuhawk_cmd.extend(["--gdi", "--chromeless"])
    print(f"[bench] launching EmuHawk port={port}", flush=True)
    proc = subprocess.Popen(
        emuhawk_cmd,
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        bridge.load_savestate(str(args.state))
        bridge.frameadvance(30)

        def capture_file_png() -> np.ndarray:
            return bridge.screenshot(shot_path)

        def capture_mmf_b64_json() -> np.ndarray:
            resp = bridge._request(
                {"cmd": "screenshot_b64", "mmf_name": mmf_name, "port": port}
            )
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "screenshot_b64 failed"))
            raw = base64.b64decode(resp["png_b64"])
            return _decode_png_bytes(raw)

        def capture_mmf_mmap() -> np.ndarray:
            resp = bridge._request(
                {"cmd": "screenshot_mmf", "mmf_name": mmf_name, "port": port}
            )
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "screenshot_mmf failed"))
            name = str(resp.get("mmf_name") or mmf_name)
            size = int(resp["size"])
            try:
                mm = mmap.mmap(-1, size, tagname=name, access=mmap.ACCESS_READ)
                try:
                    raw = mm.read(size)
                finally:
                    mm.close()
            except OSError as exc:
                raise RuntimeError(
                    f"mmf mmap read failed name={name!r} size={size}: {exc}"
                ) from exc
            return _decode_png_bytes(raw)

        methods: list[tuple[str, Callable[[], np.ndarray]]] = [
            ("file_png", capture_file_png),
            ("mmf_mmap", capture_mmf_mmap),
        ]
        if args.include_b64:
            methods.append(("mmf_b64_json", capture_mmf_b64_json))

        results: dict[str, tuple[list[float], np.ndarray]] = {}
        errors: dict[str, str] = {}
        for name, fn in methods:
            try:
                results[name] = _bench(fn, iters=int(args.iters), warmup=int(args.warmup))
                print(f"[bench] {name} ok", flush=True)
            except Exception as exc:
                errors[name] = str(exc)
                print(f"[bench] {name} FAILED: {exc}", flush=True)

        if "file_png" not in results:
            print("[bench] file_png baseline failed; aborting", flush=True)
            return 1

        ref_times, ref_rgb = results["file_png"]
        ref_gray = _resize_gray(ref_rgb)
        print("\n=== timing (Python-side capture + decode to RGB) ===", flush=True)
        print(_summarize("file_png", ref_times), flush=True)
        ref_median = statistics.median(ref_times)

        for name in ("mmf_mmap",) + (("mmf_b64_json",) if args.include_b64 else ()):
            if name not in results:
                continue
            times, rgb = results[name]
            print(_summarize(name, times), flush=True)
            speedup = ref_median / statistics.median(times)
            print(f"           vs file_png median: {speedup:.2f}x", flush=True)
            gray = _resize_gray(rgb)
            mae = float(np.mean(np.abs(gray.astype(np.int16) - ref_gray.astype(np.int16))))
            print(
                f"           parity 84x84 gray MAE vs file_png: {mae:.3f} "
                f"(shape {rgb.shape})",
                flush=True,
            )

        if errors:
            print("\n=== unavailable paths ===", flush=True)
            for name, err in errors.items():
                print(f"  {name}: {err}", flush=True)

        print(
            "\nNote: times include socket round-trip + PNG decode + (file path only) "
            "disk read/retry. Advance one frame between captures is NOT included.",
            flush=True,
        )
        return 0 if "file_png" in results else 1
    finally:
        try:
            bridge.quit()
        except (OSError, RuntimeError):
            pass
        bridge.close()
        proc.kill()
        proc.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

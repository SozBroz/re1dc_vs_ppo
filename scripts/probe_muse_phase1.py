"""Compare Muse Glimmer vs Qwen on the same Phase 1 Pass-1 shield_key prompt.

Usage:
  # Build prompt (always):
  python scripts/probe_muse_phase1.py --build-only

  # Score an existing JSON plan file:
  python scripts/probe_muse_phase1.py --score _tmp/cp05_phase1_pass1_response.json

  # Call a local OpenAI-compatible server (vLLM serving Muse or Qwen):
  python scripts/probe_muse_phase1.py --base-url http://127.0.0.1:8000/v1 --model muse-glimmer

Muse Glimmer (Meta, Apache 2.0): https://huggingface.co/meta-models/Muse-Glimmer-30B
Requires WH3 VRAM swap: stop PPO, serve Muse (or Qwen), run probe, tear down.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.phase1_route_council import (  # noqa: E402
    build_pass1_prompt,
    build_pass2_review_prompt,
    build_pass3_repair_prompt,
    build_pass4_inventory_prompt,
    build_phase1_context,
    validate_pass1_plan,
)


def _split_prompt(text: str) -> tuple[str, str]:
    parts = re.split(r"ROLE:\s*(system|user)\s*\n-{5,}\n", text)
    system = user = ""
    i = 1
    while i + 1 < len(parts):
        role = parts[i].strip().lower()
        body = parts[i + 1]
        if role == "system":
            system = re.split(r"\n={80,}\n", body, maxsplit=1)[0].strip()
        elif role == "user":
            user = body.strip()
        i += 2
    return system, user


def _extract_json(text: str) -> dict:
    text = re.sub(r"(?s)^<think>.*?</think>\s*", "", text.strip())
    start = text.find("{")
    blob = text[start:]
    plan = None
    last_err: json.JSONDecodeError | None = None
    for extra in ("", "}", "]}", "}}"):
        try:
            plan = json.loads(blob + extra)
            break
        except json.JSONDecodeError as exc:
            last_err = exc
    if plan is None:
        raise last_err or json.JSONDecodeError("no JSON object", text, 0)
    if "next_chunk" not in plan:
        for value in plan.values():
            if isinstance(value, dict) and "next_chunk" in value:
                return value
    return plan


def _score(plan: dict) -> dict:
    errors = validate_pass1_plan(plan, "cp05")
    steps = (plan.get("next_chunk") or {}).get("steps") or []
    ops = [str(s.get("op")) for s in steps]
    pickups = [str(s.get("pickup_id")) for s in steps if s.get("pickup_id")]
    sites = [str(s.get("site_id")) for s in steps if s.get("site_id")]
    return {
        "ok": not errors,
        "errors": errors,
        "n_steps": len(steps),
        "end_anchor": (plan.get("next_chunk") or {}).get("end_anchor_beat_id"),
        "has_both_kenneth_clips": (
            "104:handgun_bullets:1" in pickups and "104:handgun_bullets:2" in pickups
        ),
        "has_emblem_swap": "emblem@10F_alcove" in sites,
        "ends_on_shield_key": bool(steps)
        and str(steps[-1].get("pickup_id") or "").startswith("105:shield_key"),
        "ops": ops,
        "steps_preview": [
            {
                "n": s.get("n"),
                "op": s.get("op"),
                "edge_id": s.get("edge_id"),
                "pickup_id": s.get("pickup_id"),
                "site_id": s.get("site_id"),
            }
            for s in steps
        ],
    }


def muse_complete(
    system: str,
    user: str,
    *,
    base_url: str,
    model: str,
    max_tokens: int = 6144,
    timeout: int = 1200,
) -> tuple[dict, dict]:
    """One OpenAI-compat chat call. Returns (plan, raw)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        **({} if "muse" in str(model).lower() else {"temperature": 0.0}),
        **(
            {"chat_template_kwargs": {"reasoning_strength": "low"}}
            if "muse" in str(model).lower()
            else {}
        ),
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    msg = raw["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning_content") or ""
    plan = _extract_json(content)
    return plan, raw


def run_muse_phase1_passes(
    *,
    pass1_prompt: str,
    ctx: dict,
    base_url: str,
    model: str,
    checkpoint: str = "cp05",
) -> dict:
    """Author → loot-review Muse call → optional freebee repair if code still fails."""
    system, user = _split_prompt(pass1_prompt)
    plan, raw1 = muse_complete(system, user, base_url=base_url, model=model)
    passes = [{"name": "pass1_author", "usage": raw1.get("usage")}]
    errors = validate_pass1_plan(plan, checkpoint, ctx=ctx)
    review_prompt = build_pass2_review_prompt(plan, ctx, code_errors=errors)
    r_sys, r_user = _split_prompt(review_prompt)
    reviewed, raw2 = muse_complete(r_sys, r_user, base_url=base_url, model=model)
    if reviewed.get("next_chunk"):
        plan = reviewed
    passes.append({"name": "pass2_loot_review", "usage": raw2.get("usage")})
    errors = validate_pass1_plan(plan, checkpoint, ctx=ctx)
    if errors:
        repair_prompt = build_pass3_repair_prompt(plan, ctx, errors)
        f_sys, f_user = _split_prompt(repair_prompt)
        repaired, raw3 = muse_complete(f_sys, f_user, base_url=base_url, model=model)
        if repaired.get("next_chunk"):
            plan = repaired
        passes.append({"name": "pass3_freebee_repair", "usage": raw3.get("usage")})
        errors = validate_pass1_plan(plan, checkpoint, ctx=ctx)
    inv_prompt = build_pass4_inventory_prompt(plan, ctx, code_errors=errors)
    i_sys, i_user = _split_prompt(inv_prompt)
    refined, raw4 = muse_complete(i_sys, i_user, base_url=base_url, model=model)
    if refined.get("next_chunk") or refined.get("next_leg") or refined.get("leave_118"):
        if refined.get("leave_118"):
            plan["leave_118"] = refined["leave_118"]
        if refined.get("next_chunk"):
            plan["next_chunk"] = refined["next_chunk"]
        if refined.get("next_leg"):
            plan["next_leg"] = refined["next_leg"]
        if refined.get("beat_order"):
            plan["beat_order"] = refined["beat_order"]
        plan["inventory_review_notes"] = refined.get("review_notes")
    passes.append({"name": "pass4_inventory_refine", "usage": raw4.get("usage")})
    errors = validate_pass1_plan(plan, checkpoint, ctx=ctx)
    plan["_meta"] = {
        "model": model,
        "backend": base_url,
        "passes": passes,
        "code_audit_errors": errors,
    }
    return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--score", type=Path, default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="muse-glimmer")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "_tmp" / "muse_phase1_response.json",
    )
    args = parser.parse_args()

    prompt_path = ROOT / "_tmp" / "cp05_phase1_pass1_prompt.txt"
    prompt = build_pass1_prompt("cp05")
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"wrote {prompt_path} chars={len(prompt)}")

    if args.score:
        plan = json.loads(args.score.read_text(encoding="utf-8"))
        print(json.dumps(_score(plan), indent=2))
        return

    if args.build_only or not args.base_url:
        print(
            "Muse experiment ready.\n"
            "1) On WH3: stop PPO learner, free VRAM.\n"
            "2) Serve Muse Glimmer (or Qwen) with vLLM OpenAI API on :8000.\n"
            "3) Re-run:\n"
            "   python scripts/probe_muse_phase1.py "
            "--base-url http://127.0.0.1:8000/v1 --model <served-name>\n"
            "Model card: https://huggingface.co/meta-models/Muse-Glimmer-30B\n"
            "Compare against Qwen score:\n"
            "   python scripts/probe_muse_phase1.py "
            "--score _tmp/cp05_phase1_pass1_response.json"
        )
        if Path("_tmp/cp05_phase1_pass1_response.json").is_file():
            qwen = json.loads(
                Path("_tmp/cp05_phase1_pass1_response.json").read_text(encoding="utf-8")
            )
            print("Qwen baseline score:")
            print(json.dumps(_score(qwen), indent=2))
        return

    ctx = build_phase1_context("cp05")
    plan = run_muse_phase1_passes(
        pass1_prompt=prompt,
        ctx=ctx,
        base_url=args.base_url,
        model=args.model,
    )
    args.out.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(_score(plan), indent=2))
    print("passes", json.dumps(plan.get("_meta", {}).get("passes"), indent=2))


if __name__ == "__main__":
    main()

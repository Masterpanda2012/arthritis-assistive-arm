#!/usr/bin/env python3
"""Summarize lab_trials.jsonl for science-fair / accessibility reporting."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_trials(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _pct(successes: int, total: int) -> float | None:
    return round(100.0 * successes / total, 1) if total else None


def summarize(rows: list[dict]) -> dict:
    by_condition: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("mode", "unknown"),
            row.get("target", "unknown"),
            row.get("motor_level", "unknown"),
            bool(row.get("tremor_simulated")),
        )
        by_condition[key].append(row)

    conditions = []
    for key, group in sorted(by_condition.items()):
        mode, target, motor_level, tremor = key
        durations = [float(r["duration_s"]) for r in group if "duration_s" in r]
        corrections = [float(r.get("corrections", 0)) for r in group]
        successes = sum(1 for r in group if r.get("success"))
        vision_3d = sum(1 for r in group if r.get("used_3d_vision"))
        distances = [
            float(r["final_distance_cm"])
            for r in group
            if r.get("final_distance_cm") is not None
        ]
        align = [
            float(r["alignment_error_mm"])
            for r in group
            if r.get("alignment_error_mm") is not None
        ]
        conditions.append({
            "mode": mode,
            "target": target,
            "motor_level": motor_level,
            "tremor_simulated": tremor,
            "n": len(group),
            "success_rate_pct": _pct(successes, len(group)),
            "mean_duration_s": round(_mean(durations) or 0, 2) if durations else None,
            "mean_corrections": round(_mean(corrections) or 0, 2),
            "mean_final_distance_cm": round(_mean(distances) or 0, 2) if distances else None,
            "mean_alignment_error_mm": round(_mean(align) or 0, 2) if align else None,
            "trials_with_3d_vision": vision_3d,
        })
    return {
        "total_trials": len(rows),
        "conditions": conditions,
    }


def format_markdown(report: dict, *, min_trials: int) -> str:
    lines = [
        "# Lab Trial Summary",
        "",
        f"**Total completed trials:** {report['total_trials']}",
        "",
        f"*Target sample size per condition: **{min_trials}** trials*",
        "",
        "| Mode | Target | Motor | Tremor sim | N | Success % | Avg time (s) | Avg corrections | Avg dist (cm) | 3D vision |",
        "|------|--------|-------|------------|---|-----------|--------------|-----------------|---------------|-----------|",
    ]
    for c in report["conditions"]:
        flag = "⚠️ low N" if c["n"] < min_trials else ""
        lines.append(
            f"| {c['mode']} | {c['target']} | {c['motor_level']} | "
            f"{'yes' if c['tremor_simulated'] else 'no'} | {c['n']} {flag} | "
            f"{c['success_rate_pct'] if c['success_rate_pct'] is not None else '—'} | "
            f"{c['mean_duration_s'] if c['mean_duration_s'] is not None else '—'} | "
            f"{c['mean_corrections']} | "
            f"{c['mean_final_distance_cm'] if c['mean_final_distance_cm'] is not None else '—'} | "
            f"{c['trials_with_3d_vision']}/{c['n']} |"
        )
    lines.extend([
        "",
        "## Interpretation (arthritis accessibility)",
        "",
        "- **Lower corrections** and **shorter time** on the same target usually mean less burden on fine motor control.",
        "- Compare **manual** vs **dual_perception** / **voice** on identical objects (bottle, remote, medication).",
        "- **Tremor simulated** rows model operators who deliberately shake during manual control.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize arthritis lab trials JSONL")
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("logs/lab_trials.jsonl"),
        help="Path to lab_trials.jsonl",
    )
    parser.add_argument(
        "--min-trials",
        type=int,
        default=20,
        help="Minimum trials per condition for statistical confidence (flagged in report)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("logs/trial_summary.md"),
        help="Write markdown summary here",
    )
    parser.add_argument("--json", type=Path, help="Optional JSON export path")
    args = parser.parse_args()

    rows = load_trials(args.log)
    report = summarize(rows)
    md = format_markdown(report, min_trials=args.min_trials)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {args.out}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

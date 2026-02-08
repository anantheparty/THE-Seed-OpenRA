from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from common import PROJECT_ROOT, load_yaml, read_jsonl


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "unknown"


def dataset_stats(path: Path) -> Dict[str, Any]:
    rows = read_jsonl(path)
    intents = Counter(str(r.get("intent", "fallback_other")) for r in rows)
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "rows": len(rows),
        "intent_distribution": dict(sorted(intents.items())),
    }


def merge_distribution(parts: List[Dict[str, int]]) -> Dict[str, int]:
    c = Counter()
    for p in parts:
        for k, v in p.items():
            c[str(k)] += int(v)
    return dict(sorted(c.items()))


def prune_releases(releases_dir: Path, keep: int) -> None:
    if keep <= 0 or not releases_dir.exists():
        return
    dirs = sorted([p for p in releases_dir.iterdir() if p.is_dir()])
    if len(dirs) <= keep:
        return
    for old in dirs[: len(dirs) - keep]:
        shutil.rmtree(old, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="nlu_pipeline/configs/release_bundle.yaml")
    parser.add_argument("--train", default="nlu_pipeline/data/datasets/train.jsonl")
    parser.add_argument("--dev", default="nlu_pipeline/data/datasets/dev.jsonl")
    parser.add_argument("--test", default="nlu_pipeline/data/datasets/test.jsonl")
    parser.add_argument("--runtime-model", default="nlu_pipeline/artifacts/intent_model_runtime.json")
    parser.add_argument("--eval", default="nlu_pipeline/reports/eval_metrics.json")
    parser.add_argument("--smoke", default="nlu_pipeline/reports/smoke_report.json")
    parser.add_argument(
        "--runtime-metrics",
        "--phase4-metrics",
        dest="runtime_metrics",
        default="nlu_pipeline/reports/phase4_metrics.json",
    )
    parser.add_argument(
        "--runtime-rollback",
        "--phase4-rollback",
        dest="runtime_rollback",
        default="nlu_pipeline/reports/phase4_rollback_report.json",
    )
    parser.add_argument(
        "--runtime-runtest",
        "--phase6-runtest",
        dest="runtime_runtest",
        default="nlu_pipeline/reports/phase6_runtest_report.json",
    )
    parser.add_argument("--release-dir", default="nlu_pipeline/releases")
    parser.add_argument("--report", default="nlu_pipeline/reports/phase5_release_report.json")
    parser.add_argument("--report-md", default="nlu_pipeline/reports/phase5_release_report.md")
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    release_cfg = cfg.get("release", {})
    gate_cfg = cfg.get("gates", {})

    train_stats = dataset_stats(Path(args.train))
    dev_stats = dataset_stats(Path(args.dev))
    test_stats = dataset_stats(Path(args.test))
    runtime_model_path = Path(args.runtime_model)
    eval_metrics = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    smoke = json.loads(Path(args.smoke).read_text(encoding="utf-8"))
    runtime_metrics = json.loads(Path(args.runtime_metrics).read_text(encoding="utf-8"))
    runtime_rollback = json.loads(Path(args.runtime_rollback).read_text(encoding="utf-8"))
    runtime_runtest = json.loads(Path(args.runtime_runtest).read_text(encoding="utf-8"))

    dataset_total = int(train_stats["rows"]) + int(dev_stats["rows"]) + int(test_stats["rows"])
    merged_dist = merge_distribution(
        [
            train_stats["intent_distribution"],
            dev_stats["intent_distribution"],
            test_stats["intent_distribution"],
        ]
    )
    attack_samples = int(merged_dist.get("attack", 0))
    composite_samples = int(merged_dist.get("composite_sequence", 0))
    fallback_samples = int(merged_dist.get("fallback_other", 0))
    fallback_ratio = fallback_samples / dataset_total if dataset_total else 0.0

    failures: List[str] = []
    if dataset_total < int(gate_cfg.get("min_total_samples", 0)):
        failures.append(
            f"dataset_total {dataset_total} < min_total_samples {int(gate_cfg.get('min_total_samples', 0))}"
        )

    macro_f1 = float(eval_metrics.get("intent_macro_f1", 0.0))
    if macro_f1 < float(gate_cfg.get("min_intent_macro_f1", 0.0)):
        failures.append(
            f"intent_macro_f1 {macro_f1:.4f} < min_intent_macro_f1 {float(gate_cfg.get('min_intent_macro_f1', 0.0)):.4f}"
        )

    dangerous_fp = float(eval_metrics.get("dangerous_fp_rate", 1.0))
    if dangerous_fp > float(gate_cfg.get("max_dangerous_fp_rate", 1.0)):
        failures.append(
            f"dangerous_fp_rate {dangerous_fp:.6f} > max_dangerous_fp_rate {float(gate_cfg.get('max_dangerous_fp_rate', 1.0)):.6f}"
        )

    if attack_samples < int(gate_cfg.get("min_attack_samples", 0)):
        failures.append(
            f"attack_samples {attack_samples} < min_attack_samples {int(gate_cfg.get('min_attack_samples', 0))}"
        )

    if composite_samples < int(gate_cfg.get("min_composite_samples", 0)):
        failures.append(
            f"composite_samples {composite_samples} < min_composite_samples {int(gate_cfg.get('min_composite_samples', 0))}"
        )

    if fallback_ratio < float(gate_cfg.get("min_fallback_ratio", 0.0)):
        failures.append(
            f"fallback_ratio {fallback_ratio:.4f} < min_fallback_ratio {float(gate_cfg.get('min_fallback_ratio', 0.0)):.4f}"
        )
    if fallback_ratio > float(gate_cfg.get("max_fallback_ratio", 1.0)):
        failures.append(
            f"fallback_ratio {fallback_ratio:.4f} > max_fallback_ratio {float(gate_cfg.get('max_fallback_ratio', 1.0)):.4f}"
        )

    if bool(gate_cfg.get("require_smoke_pass", True)) and not bool(smoke.get("passed", False)):
        failures.append("smoke report not passed")

    require_runtime_rollback_not_triggered = bool(
        gate_cfg.get(
            "require_runtime_rollback_not_triggered",
            gate_cfg.get("require_phase4_rollback_not_triggered", True),
        )
    )
    if require_runtime_rollback_not_triggered and bool(runtime_rollback.get("triggered", False)):
        failures.append("runtime rollback was triggered")
    require_runtime_runtest_pass = bool(
        gate_cfg.get(
            "require_runtime_runtest_pass",
            gate_cfg.get("require_phase6_runtest_pass", True),
        )
    )
    if require_runtime_runtest_pass and not bool(runtime_runtest.get("passed", False)):
        failures.append("runtime run-test not passed")

    passed = len(failures) == 0

    now = datetime.now(timezone.utc)
    model_hash = file_sha256(runtime_model_path)[:12]
    release_id = f"release_{now.strftime('%Y%m%d_%H%M%S')}_{model_hash}"
    releases_dir = Path(args.release_dir)
    release_path = releases_dir / release_id
    release_path.mkdir(parents=True, exist_ok=True)

    artifact_copy_path = release_path / "intent_model_runtime.json"
    shutil.copy2(runtime_model_path, artifact_copy_path)

    # Snapshot key reports with normalized runtime-facing names.
    snapshots = [
        (Path(args.eval), "eval_metrics.json"),
        (Path(args.smoke), "smoke_report.json"),
        (Path(args.runtime_metrics), "runtime_metrics.json"),
        (Path(args.runtime_rollback), "runtime_rollback_report.json"),
        (Path(args.runtime_runtest), "runtime_runtest_report.json"),
    ]
    for src, dst_name in snapshots:
        shutil.copy2(src, release_path / dst_name)

    manifest = {
        "release_id": release_id,
        "channel": release_cfg.get("channel", "prod_candidate"),
        "created_at_utc": now.isoformat(),
        "git_commit": get_git_commit(),
        "passed": passed,
        "failures": failures,
        "runtime_model": {
            "path": str(artifact_copy_path),
            "sha256": file_sha256(artifact_copy_path),
        },
        "dataset": {
            "total": dataset_total,
            "attack_samples": attack_samples,
            "composite_samples": composite_samples,
            "fallback_samples": fallback_samples,
            "fallback_ratio": fallback_ratio,
            "distribution": merged_dist,
            "splits": {
                "train": train_stats,
                "dev": dev_stats,
                "test": test_stats,
            },
        },
        "metrics": {
            "intent_macro_f1": macro_f1,
            "dangerous_fp_rate": dangerous_fp,
            "runtime_latency_p95_ms": float(runtime_metrics.get("latency_ms", {}).get("p95", 0.0)),
            "runtime_route_rate": float(runtime_metrics.get("totals", {}).get("route_rate", 0.0)),
            "runtest_route_rate": float(runtime_runtest.get("totals", {}).get("route_rate", 0.0)),
        },
        "source_reports": {
            "eval_metrics": args.eval,
            "smoke_report": args.smoke,
            "runtime_metrics": args.runtime_metrics,
            "runtime_rollback": args.runtime_rollback,
            "runtime_runtest": args.runtime_runtest,
        },
    }

    (release_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    model_card_lines = [
        "# Model Card",
        "",
        f"- release_id: `{release_id}`",
        f"- git_commit: `{manifest['git_commit']}`",
        f"- runtime_model_sha256: `{manifest['runtime_model']['sha256']}`",
        f"- intent_macro_f1: `{macro_f1:.4f}`",
        f"- dangerous_fp_rate: `{dangerous_fp:.6f}`",
        f"- runtime_latency_p95_ms: `{manifest['metrics']['runtime_latency_p95_ms']:.2f}`",
        f"- runtest_route_rate: `{manifest['metrics']['runtest_route_rate']:.4f}`",
        "",
    ]
    (release_path / "model_card.md").write_text("\n".join(model_card_lines) + "\n", encoding="utf-8")

    data_card_lines = [
        "# Data Card",
        "",
        f"- release_id: `{release_id}`",
        f"- dataset_total: `{dataset_total}`",
        f"- attack_samples: `{attack_samples}`",
        f"- composite_samples: `{composite_samples}`",
        f"- fallback_ratio: `{fallback_ratio:.4f}`",
        "",
        "## Intent Distribution",
    ]
    for k, v in merged_dist.items():
        data_card_lines.append(f"- {k}: {v}")
    (release_path / "data_card.md").write_text("\n".join(data_card_lines) + "\n", encoding="utf-8")

    changelog_lines = [
        "# Release Changelog",
        "",
        f"- release_id: `{release_id}`",
        f"- created_at_utc: `{now.isoformat()}`",
        f"- git_commit: `{manifest['git_commit']}`",
        f"- smoke_passed: `{bool(smoke.get('passed', False))}`",
        f"- runtime_rollback_triggered: `{bool(runtime_rollback.get('triggered', False))}`",
        f"- runtime_runtest_passed: `{bool(runtime_runtest.get('passed', False))}`",
        "",
    ]
    (release_path / "changelog.md").write_text("\n".join(changelog_lines) + "\n", encoding="utf-8")

    manifest_out = str(release_cfg.get("manifest_out", "")).strip()
    if manifest_out:
        out_path = Path(manifest_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    prune_releases(releases_dir, int(release_cfg.get("keep_releases", 10)))

    report = {
        "passed": passed,
        "release_id": release_id,
        "release_path": str(release_path),
        "manifest_path": str(release_path / "manifest.json"),
        "failures": failures,
        "dataset_total": dataset_total,
        "intent_macro_f1": macro_f1,
        "dangerous_fp_rate": dangerous_fp,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Release Bundle Report",
        "",
        f"- result: {'PASS' if passed else 'FAIL'}",
        f"- release_id: `{release_id}`",
        f"- release_path: `{release_path}`",
        f"- dataset_total: `{dataset_total}`",
        f"- intent_macro_f1: `{macro_f1:.4f}`",
        f"- dangerous_fp_rate: `{dangerous_fp:.6f}`",
        "",
    ]
    if failures:
        md_lines.append("## Gate Failures")
        for f in failures:
            md_lines.append(f"- {f}")
    else:
        md_lines.append("## Gate Check")
        md_lines.append("- All release bundle gates passed")
    Path(args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[release_bundle] result={'PASS' if passed else 'FAIL'} release_id={release_id}")
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

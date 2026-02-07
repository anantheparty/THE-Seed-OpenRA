from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from common import PROJECT_ROOT, load_yaml, norm_text, read_jsonl, text_id, write_jsonl
from rule_weak_labeler import WeakLabeler

THE_SEED_PATH = PROJECT_ROOT / "the-seed"
if str(THE_SEED_PATH) not in sys.path:
    sys.path.insert(0, str(THE_SEED_PATH))

from the_seed.demos.openra.rules.command_dict import COMMAND_DICT, ENTITY_ALIASES, FACTION_ALIASES  # type: ignore


COUNT_CLASSIFIER = {
    "步兵": "个",
    "火箭兵": "个",
    "工程师": "个",
    "矿车": "辆",
    "装甲运输车": "辆",
    "防空车": "辆",
    "坦克": "辆",
    "重型坦克": "辆",
    "超重型坦克": "辆",
    "V2火箭车": "辆",
    "雅克战机": "架",
    "米格战机": "架",
    "电厂": "座",
    "兵营": "座",
    "矿场": "座",
    "战车工厂": "座",
    "雷达站": "座",
    "维修厂": "座",
    "核电站": "座",
    "空军基地": "座",
    "科技中心": "座",
    "火焰塔": "座",
    "特斯拉塔": "座",
    "防空导弹": "座",
}


def pick_aliases(canonical: str) -> List[str]:
    aliases = list(dict.fromkeys([canonical] + list(ENTITY_ALIASES.get(canonical, []))))
    return [a.strip() for a in aliases if str(a).strip()]


def iter_real_rows(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    freq = Counter()
    one_row: Dict[str, Dict[str, Any]] = {}
    for p in paths:
        rows = read_jsonl(p)
        for r in rows:
            text = str(r.get("text") or r.get("command") or "").strip()
            if len(text) < 2 or len(text) > 80:
                continue
            key = norm_text(text)
            if not key:
                continue
            freq[key] += 1
            if key not in one_row:
                one_row[key] = {
                    "id": text_id(text),
                    "text": text,
                    "source": "phase43_real",
                    "source_file": str(p),
                }
    out = list(one_row.values())
    out.sort(key=lambda x: (-freq[norm_text(str(x.get("text", "")))], str(x.get("text", ""))))
    for r in out:
        r["freq"] = int(freq[norm_text(str(r.get("text", "")))])
    return out


def generate_candidates() -> Dict[str, List[Dict[str, Any]]]:
    produce_verbs = COMMAND_DICT["produce"]["synonyms"][:10]
    attack_verbs = COMMAND_DICT["attack"]["synonyms"][:10]
    explore_verbs = COMMAND_DICT["explore"]["synonyms"][:10]
    mine_verbs = COMMAND_DICT["mine"]["synonyms"][:8]
    query_verbs = COMMAND_DICT["query_actor"]["synonyms"][:10]
    deploy_forms = COMMAND_DICT["deploy_mcv"]["synonyms"][:12]

    friendly_aliases = FACTION_ALIASES.get("己方", ["己方"])
    enemy_aliases = FACTION_ALIASES.get("敌方", ["敌方"])

    units = [
        "步兵",
        "火箭兵",
        "工程师",
        "矿车",
        "装甲运输车",
        "防空车",
        "坦克",
        "重型坦克",
        "超重型坦克",
        "V2火箭车",
        "雅克战机",
        "米格战机",
    ]
    buildings = [
        "电厂",
        "兵营",
        "矿场",
        "战车工厂",
        "雷达站",
        "维修厂",
        "核电站",
        "空军基地",
        "科技中心",
        "火焰塔",
        "特斯拉塔",
        "防空导弹",
    ]
    query_targets = units + buildings
    counts = list(range(1, 11)) + [12, 15, 20]

    out: Dict[str, List[Dict[str, Any]]] = {
        "produce": [],
        "attack": [],
        "query_actor": [],
        "mine": [],
        "explore": [],
        "deploy_mcv": [],
        "composite_sequence": [],
        "fallback_other": [],
    }

    polite_prefix = ["", "请", "帮我", "现在", "马上", "先"]
    polite_suffix = ["", "。", "！", "，谢谢", "，快点"]

    # produce
    for unit, cnt, verb, pref, suf in itertools.product(units + buildings, counts, produce_verbs, polite_prefix, polite_suffix):
        cls = COUNT_CLASSIFIER.get(unit, "个")
        unit_alias = pick_aliases(unit)[0]
        txt = f"{pref}{verb}{cnt}{cls}{unit_alias}{suf}".strip()
        out["produce"].append({"text": txt, "intent_hint": "produce", "source": "phase43_synth_template"})

    # attack
    attackers = units[:]
    targets = units + buildings
    for attacker, target, verb, enemy, pref, suf in itertools.product(
        attackers, targets, attack_verbs, enemy_aliases[:4], polite_prefix, polite_suffix
    ):
        a_alias = pick_aliases(attacker)[0]
        t_alias = pick_aliases(target)[0]
        txt = f"{pref}用{a_alias}{verb}{enemy}{t_alias}{suf}".strip()
        out["attack"].append({"text": txt, "intent_hint": "attack", "source": "phase43_synth_template"})

    # query
    for target, query, fac, pref, suf in itertools.product(
        query_targets, query_verbs, friendly_aliases[:4] + enemy_aliases[:4], polite_prefix, polite_suffix
    ):
        t_alias = pick_aliases(target)[0]
        txt = f"{pref}{query}{fac}{t_alias}{suf}".strip()
        out["query_actor"].append({"text": txt, "intent_hint": "query_actor", "source": "phase43_synth_template"})

    # mine
    mine_objs = ["矿车", "采矿车"]
    for verb, obj, pref, suf in itertools.product(mine_verbs, mine_objs, polite_prefix, polite_suffix):
        txt = f"{pref}让{obj}{verb}{suf}".strip()
        out["mine"].append({"text": txt, "intent_hint": "mine", "source": "phase43_synth_template"})

    # explore
    for verb, pref, suf in itertools.product(explore_verbs, polite_prefix, polite_suffix):
        txt = f"{pref}{verb}一下{suf}".strip()
        out["explore"].append({"text": txt, "intent_hint": "explore", "source": "phase43_synth_template"})

    # deploy
    for form, pref, suf in itertools.product(deploy_forms, polite_prefix, polite_suffix):
        txt = f"{pref}{form}{suf}".strip()
        out["deploy_mcv"].append({"text": txt, "intent_hint": "deploy_mcv", "source": "phase43_synth_template"})

    # composite
    comp_a = [
        "展开基地车",
        "先造2个步兵",
        "补3辆坦克",
        "侦察一下附近",
        "让矿车去采矿",
    ]
    comp_b = [
        "再造3个火箭兵",
        "然后进攻敌方矿车",
        "随后查看敌方坦克",
        "之后继续采矿",
        "接着侦察敌方基地",
    ]
    for a, b, pref, suf in itertools.product(comp_a, comp_b, polite_prefix, polite_suffix):
        txt = f"{pref}{a}然后{b}{suf}".strip()
        out["composite_sequence"].append(
            {"text": txt, "intent_hint": "composite_sequence", "source": "phase43_synth_template"}
        )

    # fallback / non-command
    fallback_texts = [
        "今天天气不错",
        "你是谁",
        "打开设置",
        "暂停游戏",
        "我想打个招呼",
        "这个地图真大",
        "音量太大了",
        "退出到主菜单",
        "这把我想稳一点",
        "现在有点卡",
        "别攻击了先停一下",
        "不要生产单位",
    ]
    for t, pref, suf in itertools.product(fallback_texts, polite_prefix, polite_suffix):
        txt = f"{pref}{t}{suf}".strip()
        out["fallback_other"].append(
            {"text": txt, "intent_hint": "fallback_other", "source": "phase43_synth_template"}
        )

    return out


def select_rows(
    *,
    real_rows: List[Dict[str, Any]],
    generated: Dict[str, List[Dict[str, Any]]],
    intent_quota: Dict[str, int],
    target_rows: int,
    real_max: int,
    seed: int,
) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    seen = set()

    # keep top real samples by observed freq
    for row in real_rows[:real_max]:
        key = norm_text(str(row.get("text", "")))
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(row)

    remaining = max(0, target_rows - len(selected))
    if remaining <= 0:
        return selected[:target_rows]

    quota_total = sum(max(0, int(v)) for v in intent_quota.values())
    scaled_quota: Dict[str, int] = {}
    if quota_total <= remaining:
        scaled_quota = {k: max(0, int(v)) for k, v in intent_quota.items()}
    else:
        # Scale down quotas proportionally so target_rows is respected while keeping intent coverage.
        remainders: List[Tuple[float, str]] = []
        used = 0
        for intent, q in intent_quota.items():
            q = max(0, int(q))
            raw = q * remaining / quota_total if quota_total else 0.0
            base = int(raw)
            scaled_quota[intent] = base
            used += base
            remainders.append((raw - base, intent))
        left = remaining - used
        for _, intent in sorted(remainders, reverse=True):
            if left <= 0:
                break
            scaled_quota[intent] = scaled_quota.get(intent, 0) + 1
            left -= 1

    # fill by scaled intent quota from synthetic candidates
    for intent, quota in scaled_quota.items():
        cands = generated.get(intent, [])[:]
        rng.shuffle(cands)
        used = 0
        for c in cands:
            key = norm_text(str(c.get("text", "")))
            if not key or key in seen:
                continue
            seen.add(key)
            selected.append(c)
            used += 1
            if used >= int(quota):
                break

    # fill remaining
    if len(selected) < target_rows:
        all_cands: List[Dict[str, Any]] = []
        for rows in generated.values():
            all_cands.extend(rows)
        rng.shuffle(all_cands)
        for c in all_cands:
            key = norm_text(str(c.get("text", "")))
            if not key or key in seen:
                continue
            seen.add(key)
            selected.append(c)
            if len(selected) >= target_rows:
                break

    return selected[:target_rows]


def enrich_with_weak_labels(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    weak = WeakLabeler()
    out: List[Dict[str, Any]] = []
    dist = Counter()
    now = int(time.time() * 1000)
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        intent_hint = row.get("intent_hint")
        inferred = weak.infer(text)
        intent = str(inferred.get("intent", "fallback_other"))
        risk_level = inferred.get("risk_level", "low")
        slots = inferred.get("slots", {})

        # For synthetic templates we trust the template intent as label truth.
        if isinstance(intent_hint, str) and intent_hint:
            intent = intent_hint
            risk_level = "high" if intent == "attack" else "low"
            if intent == "fallback_other":
                slots = {}

        dist[intent] += 1
        out.append(
            {
                "id": row.get("id") or text_id(text),
                "text": text,
                "source": row.get("source", "phase43_synth_template"),
                "source_file": row.get("source_file"),
                "intent_hint": intent_hint,
                "intent": intent,
                "slots": slots,
                "risk_level": risk_level,
                "label_source": "phase43_weak_labeler",
                "meta": {
                    "matched": bool(inferred.get("matched", False)),
                    "reason": inferred.get("reason", ""),
                    "confidence": float(inferred.get("confidence", 0.0)),
                    "collected_at_ms": now,
                },
            }
        )
    return out, dict(dist)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="nlu_pipeline/configs/phase43_collection.yaml")
    parser.add_argument("--logs", default="nlu_pipeline/data/raw/logs/commands_from_logs.jsonl")
    parser.add_argument("--web", default="nlu_pipeline/data/raw/web/commands_from_web.jsonl")
    parser.add_argument("--synth", default="nlu_pipeline/data/raw/synthetic/commands_synth.jsonl")
    parser.add_argument("--online", default="nlu_pipeline/data/raw/online/nlu_decisions.jsonl")
    parser.add_argument("--out", default="nlu_pipeline/data/raw/phase4/commands_phase43_batch.jsonl")
    parser.add_argument("--report", default="nlu_pipeline/reports/phase43_collection_report.json")
    parser.add_argument("--report-md", default="nlu_pipeline/reports/phase43_collection_report.md")
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    target_rows = int(cfg.get("target_rows", 3200))
    real_max = int(cfg.get("real_max", 600))
    seed = int(cfg.get("random_seed", 20260208))
    intent_quota = {str(k): int(v) for k, v in dict(cfg.get("intent_quota", {})).items()}

    real_rows = iter_real_rows([Path(args.logs), Path(args.web), Path(args.synth), Path(args.online)])
    generated = generate_candidates()
    selected = select_rows(
        real_rows=real_rows,
        generated=generated,
        intent_quota=intent_quota,
        target_rows=target_rows,
        real_max=real_max,
        seed=seed,
    )
    final_rows, weak_intent_dist = enrich_with_weak_labels(selected)

    out_path = Path(args.out)
    write_jsonl(out_path, final_rows)

    source_dist = Counter(str(r.get("source", "")) for r in final_rows)
    hint_dist = Counter(str(r.get("intent_hint", "")) for r in final_rows if r.get("intent_hint"))
    high_risk_count = sum(1 for r in final_rows if str(r.get("risk_level")) == "high")

    report = {
        "target_rows": target_rows,
        "collected_rows": len(final_rows),
        "real_unique_rows": len(real_rows),
        "source_distribution": dict(source_dist),
        "intent_hint_distribution": dict(hint_dist),
        "weak_intent_distribution": weak_intent_dist,
        "high_risk_rows": high_risk_count,
        "output": str(out_path),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Phase4.3 Data Collection Report",
        "",
        f"- target_rows: `{target_rows}`",
        f"- collected_rows: `{len(final_rows)}`",
        f"- real_unique_rows: `{len(real_rows)}`",
        f"- high_risk_rows: `{high_risk_count}`",
        f"- output: `{out_path}`",
        "",
        "## Source Distribution",
    ]
    for k, v in sorted(source_dist.items()):
        md_lines.append(f"- {k}: {v}")
    md_lines.extend(["", "## Weak Intent Distribution"])
    for k, v in sorted(weak_intent_dist.items()):
        md_lines.append(f"- {k}: {v}")
    Path(args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(
        f"[collect_phase43_batch] target={target_rows} collected={len(final_rows)} "
        f"real_unique={len(real_rows)} out={out_path}"
    )


if __name__ == "__main__":
    main()

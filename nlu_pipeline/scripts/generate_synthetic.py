from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Dict, List, Optional

from common import PROJECT_ROOT, text_id, write_jsonl


def load_lexicons() -> Dict[str, List[str]]:
    import sys

    the_seed_path = PROJECT_ROOT / "the-seed"
    if str(the_seed_path) not in sys.path:
        sys.path.insert(0, str(the_seed_path))

    from the_seed.demos.openra.rules.command_dict import ENTITY_ALIASES, FACTION_ALIASES  # type: ignore

    units = sorted([k for k in ENTITY_ALIASES.keys() if len(k) <= 10])
    factions = sorted(FACTION_ALIASES.keys())
    return {"units": units, "factions": factions}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="nlu_pipeline/data/raw/synthetic/commands_synth.jsonl")
    parser.add_argument("--seed", type=int, default=20260208)
    parser.add_argument("--target", type=int, default=5200)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    lex = load_lexicons()

    combat_units = [
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
    all_units = [u for u in combat_units + buildings if u in lex["units"]]

    classifiers = {
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

    prefix_pool = ["", "帮我", "给我", "麻烦", "先", "赶紧", "马上", "我这边", "这把先", "现在"]
    suffix_pool = ["", "吧", "呗", "谢谢", "快点", "。", "！", "，别停"]

    rows: List[Dict] = []
    seen = set()

    def stylize(text: str) -> str:
        pref = rng.choice(prefix_pool)
        suf = rng.choice(suffix_pool)
        out = f"{pref}{text}{suf}".strip()
        out = out.replace("  ", " ").replace("，，", "，")
        return out

    def emit(
        text: str,
        *,
        intent: str,
        slots: Optional[Dict] = None,
        risk_level: str = "low",
        source: str = "synthetic",
        label_source: str = "synthetic_colloquial",
    ) -> None:
        t = text.strip()
        if len(t) < 2 or len(t) > 80:
            return
        key = t.lower().replace(" ", "")
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "id": text_id(t),
                "text": t,
                "source": source,
                "intent": intent,
                "slots": slots or {},
                "label_source": label_source,
                "risk_level": risk_level,
                "confidence": 1.0,
            }
        )

    # produce / shorthand produce
    produce_verbs = ["造", "建造", "生产", "训练", "补", "来", "出", "整", "下"]
    for _ in range(1800):
        unit = rng.choice(all_units)
        count = rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12])
        cls = classifiers.get(unit, "个")
        verb = rng.choice(produce_verbs)

        patterns = [
            f"{verb}{count}{cls}{unit}",
            f"{count}{cls}{unit}",
            f"{unit}{count}",
            f"{verb}{unit}",
        ]
        if unit in {"电厂", "兵营", "矿场", "战车工厂"}:
            patterns.extend([f"{unit}", f"补个{unit}", f"来个{unit}"])
        if unit == "矿场":
            patterns.extend(["开矿", "开个矿", "开分矿", "补矿"])

        text = stylize(rng.choice(patterns))
        emit(
            text,
            intent="produce",
            slots={"unit": unit, "count": count, "production_items": [{"unit": unit, "count": count}]},
        )

    # mine
    mine_forms = [
        "采矿",
        "去采矿",
        "让矿车去挖矿",
        "矿车开干",
        "矿车干活",
        "拉矿",
        "采钱",
        "回矿",
        "矿车别闲着",
    ]
    for _ in range(520):
        t = stylize(rng.choice(mine_forms))
        emit(t, intent="mine", slots={"unit": "矿车", "count": 1})

    # explore
    explore_forms = ["侦察一下", "探图", "探路", "出去看看", "去开图", "看下对面", "拉个单位去侦查"]
    for _ in range(520):
        t = stylize(rng.choice(explore_forms))
        emit(t, intent="explore", slots={"count": 1})

    # deploy mcv
    deploy_forms = ["展开基地车", "部署基地车", "下基地", "开基地", "基地车展开", "把车展开"]
    for _ in range(420):
        t = stylize(rng.choice(deploy_forms))
        emit(t, intent="deploy_mcv", slots={"unit": "基地车", "count": 1})

    # query
    faction_forms = ["己方", "我方", "友军", "敌方", "对面", "对手"]
    query_verbs = ["查", "查看", "查询", "列出", "看下", "报下"]
    for _ in range(1100):
        unit = rng.choice(combat_units + buildings)
        faction = rng.choice(faction_forms)
        verb = rng.choice(query_verbs)
        patterns = [
            f"{verb}{faction}{unit}",
            f"{faction}{unit}有多少",
            f"{faction}{unit}几只",
            f"{faction}{unit}几辆",
            f"现在{faction}{unit}数量",
        ]
        t = stylize(rng.choice(patterns))
        mapped_faction = "敌方" if faction in {"敌方", "对面", "对手"} else "己方"
        emit(
            t,
            intent="query_actor",
            slots={"unit": unit, "faction": mapped_faction, "count": 1},
        )

    # attack
    attack_verbs = ["攻击", "进攻", "打", "冲", "推", "集火", "干掉", "灭了"]
    for _ in range(1000):
        attacker = rng.choice(combat_units)
        target = rng.choice(combat_units + buildings)
        verb = rng.choice(attack_verbs)
        target_side = rng.choice(["敌方", "对面", "对手"])
        patterns = [
            f"用{attacker}{verb}{target_side}{target}",
            f"{attacker}{verb}{target_side}{target}",
            f"{attacker}去{verb}{target}",
            f"让{attacker}{verb}{target_side}{target}",
        ]
        t = stylize(rng.choice(patterns))
        emit(
            t,
            intent="attack",
            slots={"attacker_type": attacker, "target_type": target, "target_faction": "敌方", "unit": target, "count": 1},
            risk_level="high",
        )

    # safe composite
    composite_a = ["展开基地车", "开矿", "造2个步兵", "补3辆坦克", "侦察一下"]
    composite_b = ["再造3个火箭兵", "然后查下对面坦克", "接着去采矿", "随后探图"]
    connectors = ["然后", "再", "接着", "随后", "之后"]
    for _ in range(700):
        a = rng.choice(composite_a)
        b = rng.choice(composite_b)
        conn = rng.choice(connectors)
        t = stylize(f"{a}{conn}{b}")
        emit(
            t,
            intent="composite_sequence",
            slots={"clauses": [a, b], "step_count": 2},
        )

    # fallback other (natural / non-command)
    fallback_texts = [
        "你在干嘛",
        "这把有点卡",
        "今天地图好大",
        "先聊会天",
        "我去喝口水",
        "等等我掉帧了",
        "音量有点大",
        "你是谁",
        "暂停一下吧",
        "退出主菜单",
        "我想打个招呼",
        "谢谢你",
        "这局别急",
        "先别动",
        "我网络波动了",
    ]
    for _ in range(900):
        t = stylize(rng.choice(fallback_texts))
        emit(t, intent="fallback_other", slots={}, risk_level="low")

    # Ensure explicit shorthand examples requested by product owner.
    forced = [
        ("电厂", "produce", {"unit": "电厂", "count": 1, "production_items": [{"unit": "电厂", "count": 1}]}),
        ("开矿", "produce", {"unit": "矿场", "count": 1, "production_items": [{"unit": "矿场", "count": 1}]}),
        ("来3个火箭兵", "produce", {"unit": "火箭兵", "count": 3, "production_items": [{"unit": "火箭兵", "count": 3}]}),
        ("给我2辆坦克", "produce", {"unit": "坦克", "count": 2, "production_items": [{"unit": "坦克", "count": 2}]}),
    ]
    for text, intent, slots in forced:
        emit(text, intent=intent, slots=slots)

    if args.target > 0 and len(rows) > args.target:
        rng.shuffle(rows)
        rows = rows[: args.target]

    write_jsonl(Path(args.out), rows)
    print(f"[generate_synthetic] rows={len(rows)} out={args.out}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import itertools
from pathlib import Path
from typing import Dict, List

from common import PROJECT_ROOT, text_id, write_jsonl


def load_lexicons() -> Dict[str, List[str]]:
    import sys

    the_seed_path = PROJECT_ROOT / "the-seed"
    if str(the_seed_path) not in sys.path:
        sys.path.insert(0, str(the_seed_path))

    from the_seed.demos.openra.rules.command_dict import ENTITY_ALIASES, FACTION_ALIASES  # type: ignore

    units = sorted([k for k in ENTITY_ALIASES.keys() if len(k) <= 8])
    factions = sorted(FACTION_ALIASES.keys())
    return {"units": units, "factions": factions}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="nlu_pipeline/data/raw/synthetic/commands_synth.jsonl")
    args = parser.parse_args()

    lex = load_lexicons()
    units = [u for u in lex["units"] if u in {"步兵", "火箭兵", "工程师", "矿车", "坦克", "重型坦克", "V2火箭车", "战车工厂", "电厂", "兵营", "矿场", "基地车"}]
    factions = [f for f in lex["factions"] if f in {"己方", "敌方"}]

    rows: List[Dict] = []

    for unit, count in itertools.product(units, [1, 2, 3, 5]):
        text = f"造{count}个{unit}" if unit not in {"坦克", "矿车", "V2火箭车", "重型坦克"} else f"造{count}辆{unit}"
        rows.append(
            {
                "id": text_id(text),
                "text": text,
                "source": "synthetic",
                "intent": "produce",
                "slots": {"unit": unit, "count": count},
                "label_source": "synthetic_template",
            }
        )

    for faction in factions:
        rows.append(
            {
                "id": text_id(f"查看{faction}坦克"),
                "text": f"查看{faction}坦克",
                "source": "synthetic",
                "intent": "query_actor",
                "slots": {"unit": "坦克", "faction": faction},
                "label_source": "synthetic_template",
            }
        )

    attacks = [
        ("坦克", "矿车"),
        ("步兵", "工程师"),
        ("重型坦克", "战车工厂"),
    ]
    for atk, tgt in attacks:
        text = f"用{atk}攻击敌方{tgt}"
        rows.append(
            {
                "id": text_id(text),
                "text": text,
                "source": "synthetic",
                "intent": "attack",
                "slots": {"attacker_type": atk, "target_type": tgt, "target_faction": "敌方"},
                "label_source": "synthetic_template",
                "risk_level": "high",
            }
        )

    for t, intent in [
        ("展开基地车", "deploy_mcv"),
        ("部署mcv", "deploy_mcv"),
        ("侦察一下", "explore"),
        ("去采矿", "mine"),
        ("先展开基地车然后造两个步兵", "composite_sequence"),
        ("今天天气不错", "fallback_other"),
        ("打开设置", "fallback_other"),
        ("我想打个招呼", "fallback_other"),
    ]:
        rows.append(
            {
                "id": text_id(t),
                "text": t,
                "source": "synthetic",
                "intent": intent,
                "slots": {},
                "label_source": "synthetic_template",
                "risk_level": "high" if intent == "attack" else "low",
            }
        )

    write_jsonl(Path(args.out), rows)
    print(f"[generate_synthetic] rows={len(rows)} out={args.out}")


if __name__ == "__main__":
    main()

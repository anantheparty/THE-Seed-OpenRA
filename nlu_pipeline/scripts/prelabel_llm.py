from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import PROJECT_ROOT, load_yaml, read_jsonl, write_jsonl
from rule_weak_labeler import WeakLabeler

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

GAME_VERB_RE = re.compile(
    r"(建造|生产|训练|制造|造|爆兵|补兵|补电|下电|下兵营|下车间|开矿|开分矿|双矿|三矿|展开|部署|下基地|攻击|进攻|突袭|集火|推家|推过去|侦察|侦查|探索|探图|采矿|挖矿|采集|拉矿|查兵|查单位|查询|查看|列出)"
)
GAME_ENTITY_RE = re.compile(
    r"(电厂|兵营|矿场|矿厂|车间|战车工厂|雷达|维修中心|核电站|科技中心|机场|火焰塔|特斯拉塔|防空炮|基地车|步兵|火箭兵|工程师|矿车|采矿车|装甲车|防空车|重坦|重型坦克|V2|v2|雅克战机|米格战机)"
)
SYSTEM_CHAT_RE = re.compile(r"(设置|菜单|暂停|退出|音量|帧率|存档|读档|天气|你好|在吗|谢谢)")
COUNT_ONLY_RE = re.compile(r"^([0-9一二三四五六七八九十两]+)(个|辆|座|架|名|只|台)?[\u4e00-\u9fffA-Za-z0-9]{1,10}$")


def parse_json_block(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def get_repo_deepseek_key() -> Optional[str]:
    main_py = PROJECT_ROOT / "main.py"
    if not main_py.exists():
        return None
    txt = main_py.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'api_key\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else None


def llm_label(
    client: Any,
    model: str,
    text: str,
    intents: List[str],
) -> Optional[Dict[str, Any]]:
    system = (
        "你是OpenRA命令NLU标注器。"
        "只输出JSON对象，不要markdown。"
        "字段: intent(string), slots(object), risk_level(low/medium/high), confidence(float 0-1)."
        "intent必须是给定枚举之一。"
    )
    user = {
        "intent_candidates": intents,
        "text": text,
        "rules": [
            "非游戏指令、聊天、系统设置请求标注为fallback_other",
            "攻击类命令风险等级至少medium，直接进攻标注high",
            "slots仅提取文本中明确出现的信息",
        ],
    }

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    )
    content = resp.choices[0].message.content if resp.choices else ""
    return parse_json_block(content or "")


def sanitize_label(raw: Dict[str, Any], intents: List[str]) -> Optional[Dict[str, Any]]:
    intent = raw.get("intent")
    if intent not in intents:
        return None
    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
    risk_level = raw.get("risk_level") if raw.get("risk_level") in {"low", "medium", "high"} else "low"
    try:
        conf = float(raw.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {
        "intent": intent,
        "slots": slots,
        "risk_level": risk_level,
        "confidence": conf,
    }


def looks_like_game_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if SYSTEM_CHAT_RE.search(t):
        # Allow explicit stop-attack commands to pass.
        if re.search(r"(停火|停止攻击|停止进攻|取消攻击|别攻击|不要攻击)", t):
            return True
        return False
    if COUNT_ONLY_RE.match(t):
        return True
    if GAME_VERB_RE.search(t):
        return True
    if GAME_ENTITY_RE.search(t) and len(t) <= 20:
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="nlu_pipeline/data/interim/unlabeled_pool.jsonl")
    parser.add_argument("--out", default="nlu_pipeline/data/labeled/prelabels.jsonl")
    parser.add_argument("--report", default="nlu_pipeline/reports/prelabel_report.json")
    parser.add_argument("--max-llm-calls", type=int, default=120)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    cfg = load_yaml(PROJECT_ROOT / "nlu_pipeline/configs/pipeline.yaml")
    schema = load_yaml(PROJECT_ROOT / "nlu_pipeline/configs/label_schema.yaml")
    intents: List[str] = list(schema.get("intents", []))

    rows = read_jsonl(Path(args.in_path))
    weak = WeakLabeler()

    api_key = args.api_key or os.getenv("NLU_LLM_API_KEY") or get_repo_deepseek_key()
    model = cfg.get("llm", {}).get("model", "deepseek-chat")
    base_url = cfg.get("llm", {}).get("base_url", "https://api.deepseek.com")

    llm_enabled = bool(api_key and OpenAI is not None)
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=cfg.get("llm", {}).get("timeout_sec", 25)) if llm_enabled else None

    llm_calls = 0
    llm_success = 0
    fallback_count = 0
    preserved = 0

    out_rows: List[Dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue

        if row.get("intent"):
            # synthetic or already labeled row
            row["label_source"] = row.get("label_source") or "prelabeled_input"
            conf_raw = row.get("confidence", 1.0)
            try:
                row["confidence"] = float(conf_raw) if conf_raw is not None else 1.0
            except (TypeError, ValueError):
                row["confidence"] = 1.0
            out_rows.append(row)
            preserved += 1
            continue

        if not looks_like_game_command(text):
            row.update(
                {
                    "intent": "fallback_other",
                    "slots": {},
                    "risk_level": "low",
                    "confidence": 0.92,
                    "label_source": "heuristic_non_command",
                }
            )
            out_rows.append(row)
            fallback_count += 1
            continue

        label_obj: Optional[Dict[str, Any]] = None
        if llm_enabled and llm_calls < args.max_llm_calls:
            llm_calls += 1
            try:
                raw = llm_label(client, model, text, intents)
                if raw is not None:
                    label_obj = sanitize_label(raw, intents)
                    if label_obj is not None:
                        llm_success += 1
            except Exception:
                label_obj = None

        if label_obj is None:
            weak_obj = weak.infer(text)
            label_obj = {
                "intent": weak_obj["intent"],
                "slots": weak_obj.get("slots", {}),
                "risk_level": weak_obj.get("risk_level", "low"),
                "confidence": float(weak_obj.get("confidence", 0.0)),
            }
            source = "weak_fallback"
            fallback_count += 1
        else:
            source = "llm_prelabel"

        row.update(label_obj)
        row["label_source"] = source
        out_rows.append(row)

    write_jsonl(Path(args.out), out_rows)

    report = {
        "input_rows": len(rows),
        "output_rows": len(out_rows),
        "llm_enabled": llm_enabled,
        "llm_calls": llm_calls,
        "llm_success": llm_success,
        "fallback_count": fallback_count,
        "preserved_labeled_rows": preserved,
        "model": model,
        "base_url": base_url,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[prelabel_llm] "
        f"rows={len(rows)} out={len(out_rows)} llm_enabled={llm_enabled} "
        f"llm_calls={llm_calls} llm_success={llm_success} fallback={fallback_count}"
    )


if __name__ == "__main__":
    main()

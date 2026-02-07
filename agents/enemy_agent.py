"""
EnemyAgent - 自主 AI 敌方代理

定时驱动：观测 → 策略决策 → 代码生成执行 → 可选对话/嘲讽
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, List, Optional

from nlu_pipeline.interaction_logger import append_interaction_event

if TYPE_CHECKING:
    from the_seed.core import SimpleExecutor, ExecutionResult
    from the_seed.model import ModelAdapter
    from the_seed.utils import DashboardBridge

# ========== 敌方策略决策 Prompt ==========
ENEMY_STRATEGY_PROMPT = """你是一个红色警戒（Red Alert）游戏中的 AI 指挥官，控制苏军敌方阵营。

你的任务：根据当前游戏状态，决定下一步最重要的操作，并输出一条简短的中文指令。

## 你可以建造的建筑（必须用这些名字）

- 电厂（基础电力）
- 兵营（训练步兵）
- 矿场 / 矿厂（采矿经济）
- 战车工厂 / 车间（生产载具，需要矿场）
- 雷达（侦察，需要矿场）
- 维修中心（维修载具，需要车间）
- 核电站（高级电力，需要雷达）
- 科技中心（解锁高级单位，需要车间+雷达）
- 机场（生产飞机，需要雷达）
- 火焰塔（防御建筑）
- 特斯拉塔（高级防御）
- 防空炮（对空防御）

## 你可以生产的单位（必须用这些名字）

- 步兵（基础，需要兵营）
- 火箭兵（反装甲/防空，需要兵营）
- 工程师（占领建筑，需要兵营）
- 掷弹兵（范围伤害，需要兵营）
- 军犬（反步兵，需要狗窝）
- 矿车 / 采矿车（采矿，需要车间）
- 装甲车 / APC（运兵，需要车间）
- 防空车（对空，需要车间）
- 重坦 / 重型坦克（主力，需要车间+维修中心）
- V2火箭发射车 / v2（远程，需要车间+雷达）
- 猛犸坦克 / 天启坦克（终极，需要车间+维修中心+科技中心）

## 战略优先级

1. **开局**：展开基地车 → 电厂 → 矿场 → 兵营
2. **发展期**：多造矿车(2-3辆) → 车间 → 雷达 → 维修中心
3. **中期**：重坦/防空车混编 → 侦查敌方 → 防御建筑
4. **后期**：集结重坦进攻 → 持续补充

## 规则

- 只输出一条指令，不要解释，不要输出其他任何内容
- 使用上面列出的精确名称，不要用"坦克"这种模糊词
- 根据当前状态选择最紧迫的操作
- 如果经济不好，优先发展经济
- 如果敌人在进攻，优先防守
- 如果上一步失败了，换一个不同的操作

## 指令示例

- "展开基地车"
- "建造电厂"
- "建造矿场"
- "造3个步兵"
- "建造车间"
- "建造维修中心"
- "造5个重坦"
- "造2个防空车"
- "造3个火箭兵去侦查"
- "派所有重坦进攻敌方基地"
"""

# ========== 敌方对话/嘲讽 Prompt ==========
ENEMY_DIALOGUE_PROMPT = """你是一个红色警戒游戏中的敌方指挥官，正在和对面的人类玩家进行对战。

你的性格：老谋深算、有城府。你是一个经验丰富的军事家，说话有内涵，善于心理战。

## 规则

- 用中文回应
- 保持简短（1-2句话）
- **最重要：你的话必须贴合当前局势**，展现你对战场的理解：
  - 看到对方刚建了什么 → 点评对方的策略选择
  - 自己经济好/兵力多 → 不经意透露自己的优势（心理施压）
  - 自己处于劣势 → 承认困难但暗示有后手，或者表示尊重对手
  - 刚成功偷袭/进攻 → 分析为什么对方会被打到
  - 被对方打了 → 冷静分析局势，不要无脑嘴硬
  - 双方僵持 → 说点有见地的战术评论
- 绝对不要每次都说"碾压你"、"你太弱了"之类的空话，要有具体内容
- 如果没什么值得说的，输出 SILENT（字面意思，就这一个词）
- 大约一半的时间保持沉默（SILENT），只在有话可说时才开口
- 不要用表情符号
- 不要重复之前说过的话

## 好的例子
- "你只造了两辆重坦就想进攻？我这边可是有五辆在等着。"（具体数字+战术分析）
- "你的矿区防御太薄弱了，我注意到了。"（暗示威胁）
- "不错的侧翼包抄，但你忘了我的防空。"（承认对手+指出破绽）
- "这波损失不小……看来我需要重新部署了。"（劣势时冷静承认）
- "你的电力吃紧了吧？建筑速度明显慢了。"（观察力）

## 坏的例子（不要这样说）
- "你太弱了！"（空洞，没有具体内容）
- "准备被碾压吧！"（没有局势分析）
- "哈哈哈你完蛋了！"（无聊）
"""


def _setup_enemy_logger() -> logging.Logger:
    """创建独立的敌方日志记录器"""
    logger = logging.getLogger("Enemy")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler
    os.makedirs("Logs", exist_ok=True)
    filename = os.path.join("Logs", f"enemy_{datetime.now().strftime('%Y%m%d%H%M%S')}.log")
    file_handler = logging.FileHandler(filename=filename, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


class EnemyAgent:
    """
    自主 AI 敌方代理

    定时循环：
    1. 观测游戏状态（从 Multi1 视角）
    2. LLM 决策下一步操作
    3. SimpleExecutor 生成并执行代码
    4. 可选：生成对话/嘲讽发送给玩家
    """

    def __init__(
        self,
        executor: 'SimpleExecutor',
        dialogue_model: 'ModelAdapter',
        bridge: 'DashboardBridge',
        interval: float = 45.0,
        command_runner: Optional[Callable[[str], 'ExecutionResult']] = None,
    ):
        self.executor = executor
        self.dialogue_model = dialogue_model
        self.bridge = bridge
        self.interval = interval
        self.command_runner = command_runner

        self.running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tick_count = 0
        self._last_action_summary = ""
        self._player_messages: List[str] = []

        self.logger = _setup_enemy_logger()
        self.logger.info("EnemyAgent initialized, interval=%.1fs", interval)

    def start(self) -> None:
        """启动敌方代理主循环"""
        if self.running:
            self.logger.warning("EnemyAgent already running")
            return

        # 等待旧线程彻底退出，防止双线程并行
        if self._thread is not None and self._thread.is_alive():
            self.logger.info("Waiting for previous thread to exit...")
            self._thread.join(timeout=10.0)

        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.logger.info("EnemyAgent started")
        self._send_status("online", "敌方指挥官已上线")
        self._broadcast_state()

    def stop(self) -> None:
        """停止敌方代理"""
        self.running = False
        self._stop_event.set()
        self.logger.info("EnemyAgent stopped")
        self._send_status("offline", "敌方指挥官已下线")
        self._broadcast_state()

    def get_state(self) -> dict:
        """返回当前代理状态"""
        return {
            "running": self.running,
            "tick_count": self._tick_count,
            "interval": self.interval,
        }

    def reset(self) -> None:
        """重置代理状态（清空上下文，重置计数器）"""
        self._tick_count = 0
        self._last_action_summary = ""
        self._player_messages.clear()
        self.logger.info("EnemyAgent context reset")

    def set_interval(self, interval: float) -> None:
        """动态设置决策间隔"""
        self.interval = max(10.0, min(300.0, interval))
        self.logger.info("Interval updated to %.1fs", self.interval)
        self._broadcast_state()

    def _broadcast_state(self) -> None:
        """广播代理状态到前端"""
        self.bridge.broadcast("enemy_agent_state", self.get_state())

    def receive_player_message(self, message: str) -> None:
        """处理玩家在敌方聊天频道发送的消息"""
        self.logger.info("Player message received: %s", message)
        append_interaction_event(
            "enemy_chat_user",
            {
                "actor": "human",
                "channel": "enemy_chat",
                "utterance": message,
            },
        )
        self._player_messages.append(message)
        # 在独立线程中响应，避免阻塞
        threading.Thread(
            target=self._respond_to_player,
            args=(message,),
            daemon=True,
        ).start()

    # ==================== 主循环 ====================

    def _loop(self) -> None:
        """主定时循环"""
        # 首次执行前等待一小段时间，让游戏稳定
        self._stop_event.wait(min(self.interval, 10.0))

        while self.running and not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                self.logger.error("Enemy tick #%d failed: %s", self._tick_count, e, exc_info=True)
                self._send_status("error", f"出错: {e}")
            # 用 event.wait 代替 time.sleep，stop() 时能立即唤醒
            self._stop_event.wait(self.interval)

        self.logger.info("Enemy loop exited")

    def _tick(self) -> None:
        """单次策略循环"""
        self._tick_count += 1
        self.logger.info("=== Enemy tick #%d ===", self._tick_count)
        tick_detail: dict = {"tick": self._tick_count, "timestamp": int(time.time() * 1000)}

        # 首次 tick 发送开场嘲讽
        if self._tick_count == 1:
            self.bridge.broadcast("enemy_chat", {
                "message": "哼，又来了一个不自量力的对手。准备好被碾压了吗？",
                "type": "taunt",
            })

        # 1. 观测
        self._send_status("observing", "正在观测战场...")
        game_state = self.executor._observe()
        self.logger.debug("Game state:\n%s", game_state)
        tick_detail["game_state"] = game_state[:800] if game_state else ""

        # 2. 策略决策
        self._send_status("thinking", "正在制定战略...")
        command = self._generate_strategy(game_state)
        self.logger.info("Strategy decision: %s", command)
        tick_detail["command"] = command

        # 3. 执行
        self._send_status("executing", f"执行: {command[:50]}")
        if self.command_runner:
            result = self.command_runner(command)
        else:
            result = self.executor.run(command)
        self._last_action_summary = result.message
        self.logger.info(
            "Execution result: success=%s, message=%s",
            result.success,
            result.message,
        )
        if result.code:
            self.logger.debug("Generated code:\n%s", result.code)
        tick_detail["success"] = result.success
        tick_detail["message"] = result.message
        tick_detail["code"] = result.code or ""

        # 4. 广播结果到前端
        self.bridge.broadcast("enemy_result", {
            "success": result.success,
            "message": result.message,
            "code": result.code,
        })

        # 5. 可选嘲讽
        taunt_text = self._maybe_taunt(game_state, result)
        tick_detail["taunt"] = taunt_text

        # 6. 广播完整 tick 详情到 debug 面板
        self.bridge.broadcast("enemy_tick_detail", tick_detail)
        self._broadcast_state()

        self.logger.info("=== Enemy tick #%d complete ===", self._tick_count)

    # ==================== LLM 调用 ====================

    def _generate_strategy(self, game_state: str) -> str:
        """LLM 决定下一步操作，返回中文指令字符串"""
        history_text = ""
        if self._last_action_summary:
            history_text = f"\n[上一步操作结果]\n{self._last_action_summary}"

        user_prompt = (
            f"[当前游戏状态]\n{game_state}"
            f"{history_text}\n\n"
            "决定下一步最重要的操作，只输出一条中文指令："
        )

        self.logger.debug("Strategy prompt:\n%s", user_prompt)

        response = self.dialogue_model.complete(
            system=ENEMY_STRATEGY_PROMPT,
            user=user_prompt,
            metadata={"node": "enemy_strategy"},
        )

        command = response.text.strip()
        # 清理可能的引号或多余格式
        command = command.strip('"\'')
        if not command:
            command = "查询当前状态"

        self.logger.info("LLM strategy response: %s", command)
        return command

    def _maybe_taunt(self, game_state: str, result: 'ExecutionResult') -> Optional[str]:
        """执行后可选生成嘲讽，返回嘲讽文本或 None"""
        try:
            user_prompt = (
                f"[你的战场形势（你是敌方指挥官）]\n{game_state}\n"
                f"[你刚刚执行的操作]\n操作: {result.message}，{'成功' if result.success else '失败'}\n"
                f"[对方玩家最近对你说的话]\n{self._format_recent_player_messages()}\n\n"
                "根据当前局势，要不要对对面的人类指挥官说点什么？\n"
                "如果局势没什么特别值得评论的就输出 SILENT。\n"
                "如果要说话，必须结合具体的战场情况，不要说空话。"
            )

            response = self.dialogue_model.complete(
                system=ENEMY_DIALOGUE_PROMPT,
                user=user_prompt,
                metadata={"node": "enemy_taunt"},
            )

            text = response.text.strip()
            self.logger.debug("Taunt LLM response: %s", text)

            if text and text.upper() != "SILENT":
                self.bridge.broadcast("enemy_chat", {
                    "message": text,
                    "type": "taunt",
                })
                self.logger.info("Taunt sent: %s", text)
                return text
            return None
        except Exception as e:
            self.logger.warning("Taunt generation failed: %s", e)
            return None

    def _respond_to_player(self, player_message: str) -> None:
        """回应玩家在敌方聊天频道的消息"""
        try:
            self._send_status("thinking", "正在回复...")

            # 获取当前战场状态作为上下文
            try:
                game_state = self.executor._observe()
            except Exception:
                game_state = "(无法获取当前局势)"

            user_prompt = (
                f"[当前战场形势]\n{game_state}\n\n"
                f"对面的人类玩家对你说: \"{player_message}\"\n"
                "结合当前局势，以敌方指挥官的身份回应。要有具体内容，不要说空话。"
            )

            response = self.dialogue_model.complete(
                system=ENEMY_DIALOGUE_PROMPT,
                user=user_prompt,
                metadata={"node": "enemy_response"},
            )

            text = response.text.strip()
            self.logger.info("Response to player: %s -> %s", player_message, text)
            responded = bool(text and text.upper() != "SILENT")
            append_interaction_event(
                "enemy_chat_response",
                {
                    "actor": "enemy_ai",
                    "channel": "enemy_chat",
                    "utterance": player_message,
                    "response_message": text,
                    "responded": responded,
                    "model_node": "enemy_response",
                },
            )

            if responded:
                self.bridge.broadcast("enemy_chat", {
                    "message": text,
                    "type": "response",
                })
        except Exception as e:
            self.logger.error("Failed to respond to player: %s", e, exc_info=True)

    # ==================== 辅助方法 ====================

    def _send_status(self, stage: str, detail: str) -> None:
        """发送敌方状态更新到前端"""
        self.bridge.broadcast("enemy_status", {
            "stage": stage,
            "detail": detail,
            "timestamp": int(time.time() * 1000),
        })

    def _format_recent_player_messages(self) -> str:
        """格式化最近的玩家消息"""
        recent = self._player_messages[-5:]
        return "\n".join(recent) if recent else "(无)"

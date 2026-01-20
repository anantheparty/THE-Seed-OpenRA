from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import threading

@dataclass
class Signal:
    """标准信号类，用于 Agent 间通信"""
    sender: str
    receiver: str  # "all" or specific agent name
    type: str      # e.g., "request_escort", "resource_found"
    payload: Any
    ttl: int = 10  # Time to live (ticks)

class GlobalBlackboard:
    """
    全局黑板，作为所有 Agent 共享的数据交换中心。
    虽然目前是单线程轮询，但预留了锁机制以支持未来可能的并发。
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        # 1. 指令区：来自用户的最高指令
        self.command: Optional[str] = None
        
        # 2. 信号市场：Agent 发布的请求和广播
        # Key: signal_id, Value: Signal
        self.market: List[Signal] = []
        
        # 3. 公共情报：全局态势感知（如热力图、迷雾状态）
        # 避免各 Agent 重复计算昂贵的信息
        self.intelligence: Dict[str, Any] = {
            "heatmap": None,
            "enemy_threat_level": 0.0,
            "resource_spots": []
        }
        
        # 4. 注册的 Agent 列表 (用于调试和状态监控)
        self.registered_agents: Dict[str, str] = {} # name -> status

    def publish_signal(self, signal: Signal):
        """发布一个信号到市场"""
        with self._lock:
            self.market.append(signal)

    def consume_signals(self, receiver_name: str) -> List[Signal]:
        """获取发给指定 Agent 的信号（不删除广播信号，只筛选）"""
        # 注意：这里简化处理，实际可能需要更复杂的消费逻辑（是否已读等）
        # 目前返回所有目标为 receiver 或 "all" 的信号
        with self._lock:
            return [
                s for s in self.market 
                if s.receiver == receiver_name or s.receiver == "all"
            ]

    def clear_expired_signals(self):
        """清理过期信号 (TTL <= 0)"""
        with self._lock:
            self.market = [s for s in self.market if s.ttl > 0]
            for s in self.market:
                s.ttl -= 1

    def update_intelligence(self, key: str, value: Any):
        """更新公共情报"""
        with self._lock:
            self.intelligence[key] = value

    def get_intelligence(self, key: str) -> Any:
        """获取公共情报"""
        with self._lock:
            return self.intelligence.get(key)

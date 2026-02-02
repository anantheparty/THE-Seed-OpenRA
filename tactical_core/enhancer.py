# -*- coding: utf-8 -*-
"""
Biods 算法增强 V2.0 (Tactical Core Facade)
作为战术核心模块的统一入口，协调以下子模块：
1. EntityManager: 战术实体状态管理
2. DecisionGuard: 目标管理与协同回退
3. PotentialField: 实时势场微操
4. InterruptLogic: 战术硬中断
"""
import threading
import time
import os
from typing import List, Tuple, Optional, Any, Union

from .entity_manager import EntityManager
from .decision_guard import DecisionGuard
from .potential_field import PotentialField
from .interrupt_logic import InterruptLogic
from .client import TacticalClient
from .ui import TacticalLogWindow

from .constants import UnitCategory

class BiodsEnhancer:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._running = False
        self._manager_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        
        # 内部独立客户端
        self._client: Optional[TacticalClient] = None
        
        # 日志窗口
        self._log_window: Optional[TacticalLogWindow] = None
        
        # 子模块实例
        self.em: Optional[EntityManager] = None
        self.decision_guard: Optional[DecisionGuard] = None
        self.potential_field: Optional[PotentialField] = None
        self.interrupt_logic: Optional[InterruptLogic] = None
        
        self._tick_count = 0
        # 延迟恢复队列 {tick_to_execute: [pairs]}
        self._delayed_attack_restores = {}

    def start(self, api_client_placeholder, show_log_window: bool = False) -> None:
        """启动战术核心后台线程"""
        with self._lock:
            if self._running:
                return
            
            # 初始化独立客户端
            self._client = TacticalClient()
            
            # 初始化子模块 (注入独立客户端)
            self.em = EntityManager(self._client)
            self.decision_guard = DecisionGuard(self.em)
            self.potential_field = PotentialField(self.em)
            self.interrupt_logic = InterruptLogic(self.em)
            
            self._running = True
            self._manager_thread = threading.Thread(target=self._manager_loop, name="TacticalCoreLoop", daemon=True)
            self._manager_thread.start()
            
            if show_log_window:
                self.show_log_window()
            
            self._log_debug("Tactical Core V2 started")

    def show_log_window(self):
        """显示日志窗口"""
        with self._lock:
            if not self._log_window:
                self._log_window = TacticalLogWindow()
                self._log_window.start()
                self._log_debug("Log Window Initialized")
            elif hasattr(self._log_window, 'show'):
                # 如果 UI 支持 show/hide
                self._log_window.show()

    def hide_log_window(self):
        """隐藏日志窗口"""
        with self._lock:
            if self._log_window and hasattr(self._log_window, 'hide'):
                self._log_window.hide()
            elif self._log_window:
                # 如果不支持 hide，则销毁
                self._log_window.stop()
                self._log_window = None

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._log_window:
                try:
                    self._log_window.stop()
                except Exception:
                    pass
                self._log_window = None
            t = self._manager_thread
            self._manager_thread = None
        if t:
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
            self._log_debug("Tactical Core V2 stopped")

    def enhance_execute(self, api_client_placeholder, pairs: List[Tuple[int, int]]) -> Tuple[bool, str]:
        """
        上游接口适配：接收 LLM/战斗专家的目标分配
        """
        if not self._running:
            self.start(None)
            
        if self.decision_guard:
            # 1. 将新分配注入决策层
            self.decision_guard.process_decisions(pairs)
            
            # 2. 移除立即下发，交由主循环统一调度，确保硬中断优先级
            # 之前版本为了响应速度直接下发，现在为了保证硬中断（如装甲收割）不被覆盖，
            # 统一由 _manager_loop 在下一帧处理。延迟极低(<=0.1s)，可以接受。
            
            return True, f"TacticalCore: Received {len(pairs)} assignments"
            
        return False, "TacticalCore not initialized"

    def _manager_loop(self) -> None:
        """战术核心主循环"""
        while True:
            with self._lock:
                if not self._running:
                    break
            
            try:
                if not self.em or not self.decision_guard:
                    time.sleep(0.1)
                    continue

                # 1. 状态同步 (约 10Hz)
                self.em.update()
                
                # 简单统计活跃单位
                active_ally_count = sum(1 for a in self.em.allies.values() if a.is_active)
                active_enemy_count = sum(1 for e in self.em.enemies.values() if e.is_active)
                if self._tick_count % 50 == 0: # 每5秒左右输出一次心跳
                    self._log_debug(f"Tick {self._tick_count}: Allies={active_ally_count}, Enemies={active_enemy_count}")

                # 2. 目标维护
                active_pairs = self.decision_guard.process_decisions([])
                
                # 3. 硬中断
                interrupt_moves, interrupt_attacks = self.interrupt_logic.check_interrupts()
                
                # 收集被硬中断占用的单位ID
                interrupted_units = set()
                interrupted_units.update(interrupt_moves.keys())
                interrupted_units.update(att[0] for att in interrupt_attacks)
                
                # 4. 势场微操 (排除被硬中断单位)
                active_allies = [a for a in self.em.allies.values() if a.is_active and a.actor_id not in interrupted_units]
                pf_moves = self.potential_field.calculate_moves(active_allies)
                
                # 记录因势场微操而移动的单位，稍后需要恢复其攻击任务
                pf_moved_units = set(pf_moves.keys())
                
                # 合并移动 (硬中断优先)
                final_moves = pf_moves.copy()
                final_moves.update(interrupt_moves)
                self._execute_moves(final_moves)
                
                # 5. 执行攻击指令维护
                self._tick_count += 1
                
                # 5.1 处理延迟恢复的攻击任务 (硬中断结束后的回归)
                if self._tick_count in self._delayed_attack_restores:
                    delayed_pairs = self._delayed_attack_restores.pop(self._tick_count)
                    # 再次过滤，确保单位未被硬中断接管
                    valid_delayed = [p for p in delayed_pairs if p[0] not in interrupted_units]
                    if valid_delayed:
                        # 恢复任务需要完全显示 Log (log=True)
                        self._execute_attacks(valid_delayed, log=True)

                # 5.2 硬中断攻击指令 (取消限频，每一帧都执行以确保 Log 实时显示)
                if interrupt_attacks:
                    self._execute_attacks(interrupt_attacks)

                # 5.3 常规攻击指令 (取消 10 帧限频，确保指令下发的实时性，仅 Log 做区分)
                # 过滤掉已被硬中断攻击接管的单位，防止上游指令覆盖硬中断
                filtered_active_pairs = [
                    p for p in active_pairs 
                    if p[0] not in interrupted_units
                ]
                
                # 区分 Log 显示逻辑：
                # 1. 上游分配指令 (reason 为空或包含"上游指令") -> 不显示 Log (log=False)
                # 2. 协同回退/自动接管 (reason 包含"协同回退") -> 完全显示 Log (log=True)
                
                upstream_pairs = []
                cohesion_pairs = []
                
                for p in filtered_active_pairs:
                    reason = p[2] if len(p) > 2 else ""
                    if "协同回退" in reason:
                        cohesion_pairs.append(p)
                    else:
                        upstream_pairs.append(p)
                
                # 执行上游指令 (静默)
                if upstream_pairs:
                    self._execute_attacks(upstream_pairs, log=False)
                    
                # 执行协同回退 (显示 Log)
                if cohesion_pairs:
                    self._execute_attacks(cohesion_pairs, log=True)
                pass

            except Exception as e:
                self._log_debug(f"Loop error: {e}")

            except Exception as e:
                self._log_debug(f"Loop error: {e}")
            
            time.sleep(0.1)

    def _execute_moves(self, moves: dict) -> None:
        if not moves or not self._client:
            return
        # 按方向分组以备未来优化，目前简单遍历
        for aid, move_data in moves.items():
            direction = None
            distance = 1
            reason = ""
            
            # 兼容处理: 支持 (direction, distance, reason)
            if isinstance(move_data, (tuple, list)):
                direction = move_data[0]
                if len(move_data) > 1:
                    distance = int(move_data[1])
                if len(move_data) > 2:
                    reason = str(move_data[2])
            else:
                direction = str(move_data)
                distance = 1

            # 战术移动策略：
            # 1. 默认：assault=False, is_attack_move=False (纯移动，最高优先级)
            # 2. 脆皮脱离：必须是纯移动
            # 3. MBT势场微调：启用 assault=True 以碾压步兵，但仍保持 is_attack_move=False 避免被牵制
            
            is_assault = False
            
            # 判断是否为 MBT 且非硬中断逃跑
            # 注意：这里需要从 em 中获取单位信息来判断类型
            if self.em:
                entity = self.em.get_entity(aid)
                if entity and entity.category == UnitCategory.MBT:
                    # 如果不是硬中断逃跑 (reason 中不包含"脱离")，则开启碾压
                    # 硬中断逃跑通常带有 "[战术硬中断:脆皮脱离]" 标签
                    if "脱离" not in reason:
                        is_assault = True

            self._client.move_unit(aid, direction, distance=distance, assault=is_assault, is_attack_move=False)
            self._log_debug(f"{reason} Move: Unit {aid} -> {direction} ({distance}) [Assault={is_assault}]")

    def _execute_attacks(self, pairs: List[Union[Tuple[int, int], Tuple[int, int, str]]], log: bool = True) -> None:
        if not pairs or not self._client:
            return
        for item in pairs:
            aid = item[0]
            tid = item[1]
            reason = item[2] if len(item) > 2 else ""
            
            self._client.attack_target(aid, tid)
            if log:
                self._log_debug(f"{reason} Attack: Unit {aid} -> Target {tid}")

    def _log_debug(self, msg: str) -> None:
        debug_on = str(os.environ.get("LLM_DEBUG", "0")).lower() in ("1", "true", "yes")
        
        if self._log_window:
            self._log_window.log(msg)
            
        if debug_on:
             print(f"[TacticalCore] {msg}")

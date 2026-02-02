from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .actor_utils import select_combat_units
from .game_api import GameAPI, GameAPIError
from .intel.service import IntelService
from .jobs import JobManager
from .models import Actor, FrozenActor, Location, TargetsQueryParam


class MacroActions:
    """对 GameAPI 的“宏操作”封装：尽量保持调用方式与 GameAPI 一致。

    设计目标
    - 让玩家以 **GameAPI 风格** 使用“宏”能力：入参尽量沿用 `Actor` / `TargetsQueryParam` / `Location` 等。
    - 本模块只做 **一次性** 调用封装；不做长循环、策略、持续调度（除非底层 GameAPI 方法本身就是 *_wait）。
    - “探索/攻击”不直接发命令，而是通过 `JobManager` **显式分配** actor->job（避免 job 之间抢人）。
    """

    def __init__(
        self,
        api: GameAPI,
        intel: Optional[IntelService] = None,
        jobs: Optional[JobManager] = None,
    ) -> None:
        """初始化 MacroActions。

        Args:
            api (GameAPI): 游戏控制接口（底层 RPC）。
            intel (Optional[IntelService]): 情报服务（可选）。本类的大多数方法不依赖它。
            jobs (Optional[JobManager]): Job 管理器（可选）。用于 dispatch_explore/dispatch_attack。
        """
        self.api = api
        self.intel = intel
        self.jobs = jobs

    # ----------------------------
    # 生产相关
    # ----------------------------
    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = False) -> Optional[int]:
        """生产指定数量的 Actor（一次性下单，不等待）。

        Args:
            unit_type (str): Actor 类型（中文名），如 "步兵"、"电厂"。
            quantity (int): 数量。
            auto_place_building (bool): 若为建筑，生产完成后是否自动放置（由服务端实现）。

        Returns:
            Optional[int]: waitId（用于后续 wait/query），失败返回 None。

        Raises:
            GameAPIError: 当 RPC 返回错误时抛出。
        """
        return self.api.produce(unit_type, quantity, auto_place_building=auto_place_building)

    def produce_wait(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> None:
        """生产并等待完成（会阻塞，内部轮询 wait）。

        Args:
            unit_type (str): Actor 类型（中文名）。
            quantity (int): 数量。
            auto_place_building (bool): 若为建筑，生产完成后是否自动放置。

        Raises:
            GameAPIError: 当生产/等待过程中失败时抛出。
        """
        self.api.produce_wait(unit_type, quantity, auto_place_building=auto_place_building)

    def ensure_can_build_wait(self, building_name: str) -> bool:
        """确保拥有/可生产某建筑的前置（可能会自动生产前置并等待）。

        Args:
            building_name (str): 建筑名称（中文名），如 "电厂"、"矿场"。

        Returns:
            bool: 是否已经满足/准备好该建筑的生产条件。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        return self.api.ensure_can_build_wait(building_name)

    def ensure_can_produce_unit(self, unit_name: str) -> bool:
        """确保拥有/可生产某单位的前置（可能会自动生产前置建筑并等待）。

        Args:
            unit_name (str): 单位名称（中文名），如 "步兵"、"矿车"。

        Returns:
            bool: 是否已经满足/准备好该单位的生产条件。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        return self.api.ensure_can_produce_unit(unit_name)

    # ----------------------------
    # 展开/采矿
    # ----------------------------
    def deploy_mcv_and_wait(self, wait_time: float = 1.0) -> None:
        """展开基地车并等待一小会（会阻塞）。

        Args:
            wait_time (float): 展开后的等待时间（秒）。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.deploy_mcv_and_wait(wait_time=wait_time)

    def harvester_mine(self, harvesters: Sequence[Actor]) -> None:
        """采矿车采矿：实现为对采矿车执行 deploy（一次性指令，不等待）。

        Args:
            harvesters (Sequence[Actor]): 采矿车 actor 列表。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.deploy_units(list(harvesters))

    def deploy(self, actors: Sequence[Actor]) -> None:
        """对一组 actor 执行 deploy/展开（一次性指令，不等待）。

        Args:
            actors (Sequence[Actor]): 要 deploy 的 actor 列表。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.deploy_units(list(actors))

    # ----------------------------
    # Job 分配（探索/攻击）
    # ----------------------------
    def dispatch_explore(self, actors: Sequence[Actor]) -> None:
        """派遣某个单位探索：把 actor 显式分配到某个 ExploreJob（不直接发移动命令）。

        说明：
            - Job 本质是 mid layer 维护的 actor 状态，不来自游戏。
            - actor 同时最多属于一个 job；如果已在其他 job，会被自动解绑后再绑定。

        Args:
            actors (Sequence[Actor]): 要派遣的单位列表。

        Raises:
            ValueError: 当 JobManager 未提供时抛出。
        """
        job_id: str = "explore"
        mgr = self.jobs
        if mgr is None:
            raise ValueError("MacroActions.dispatch_explore 需要 JobManager（构造时传入或调用时传 jobs=）")
        for actor in actors:
            mgr.assign_actor_to_job(actor, job_id)

    def dispatch_attack(self, actors: Sequence[Actor]) -> None:
        """派遣某个单位攻击：把 actor 显式分配到某个 AttackJob（不直接发攻击命令）。

        Args:
            actors (Sequence[Actor]): 要派遣的单位列表。

        Raises:
            ValueError: 当 JobManager 未提供时抛出。
        """
        job_id: str = "attack"
        mgr = self.jobs
        if mgr is None:
            raise ValueError("MacroActions.dispatch_attack 需要 JobManager（构造时传入或调用时传 jobs=）")
        for actor in actors:
            mgr.assign_actor_to_job(actor, "attack")

    # ----------------------------
    # 编组/选择/查询
    # ----------------------------
    def form_group(self, actors: Sequence[Actor], group_id: int) -> None:
        """将一组 actor 编入指定编组。

        Args:
            actors (Sequence[Actor]): 要编组的 actor 列表。
            group_id (int): 编组 ID。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.form_group(list(actors), group_id)

    def select_units(self, query_params: TargetsQueryParam) -> None:
        """执行一次“选中单位”的游戏操作。

        Args:
            query_params (TargetsQueryParam): 选择条件（范围/阵营/类型等）。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.select_units(query_params)

    def query_actor(self, query_params: TargetsQueryParam) -> List[Actor]:
        """查询符合条件的 actor 列表（只返回可见/可查询到的 actor）。

        Args:
            query_params (TargetsQueryParam): 查询条件（参考 GameAPI.query_actor）。

        Returns:
            List[Actor]: actor 列表（包含 position/hppercent/activity/order 等）。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        return self.api.query_actor(query_params)

    def query_combat_units(self) -> List[Actor]:
        """查询己方所有战斗单位（自动排除矿车、工程师、基地车等非战斗单位）。

        Returns:
            List[Actor]: 战斗单位列表。
        """
        all_mine = self.api.query_actor(TargetsQueryParam(faction="自己"))
        return select_combat_units(all_mine)

    def unit_attribute_query(self, actors: Sequence[Actor]) -> Dict[str, Any]:
        """查询单位属性与攻击范围内目标。

        Args:
            actors (Sequence[Actor]): 要查询的 actor 列表。

        Returns:
            Dict[str, Any]: 服务端返回的属性结构（原样透传）。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        return self.api.unit_attribute_query(list(actors))

    # ----------------------------
    # 生产队列
    # ----------------------------
    def query_production_queue(self, queue_type: str) -> Dict[str, Any]:
        """查询指定类型的生产队列。

        Args:
            queue_type (str): 队列类型：Building/Defense/Infantry/Vehicle/Aircraft/Naval。

        Returns:
            Dict[str, Any]: 队列信息结构（原样透传）。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        return self.api.query_production_queue(queue_type)

    def place_building(self, queue_type: str, location: Optional[Location] = None) -> None:
        """放置建造队列顶端已就绪的建筑/防御（一次性指令，不等待）。

        Args:
            queue_type (str): Building 或 Defense（与 GameAPI.place_building 一致）。
            location (Optional[Location]): 放置位置；None 表示由服务端自动选择。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.place_building(queue_type, location=location)

    def manage_production(self, queue_type: str, action: str) -> None:
        """管理生产队列（暂停/取消/继续）。

        Args:
            queue_type (str): 队列类型：Building/Defense/Infantry/Vehicle/Aircraft/Naval。
            action (str): 操作：'pause' / 'cancel' / 'resume'。

        Raises:
            GameAPIError: 当 RPC 调用失败时抛出。
        """
        self.api.manage_production(queue_type, action)

    # ----------------------------
    # 含残影查询
    # ----------------------------
    def query_actor_with_frozen(self, query_params: TargetsQueryParam) -> Tuple[List[Actor], List[FrozenActor]]:
        """查询符合条件的 actor，同时返回残影（FrozenActor）。

        残影是之前见过但现在被战争迷雾覆盖的建筑/单位，保留最后已知位置。

        Args:
            query_params (TargetsQueryParam): 查询条件。

        Returns:
            Tuple[List[Actor], List[FrozenActor]]:
                - actors: 当前可见的 actor 列表
                - frozen_actors: 残影列表（有 type/faction/position）
        """
        return self.api.query_actorwithfrozen(query_params)

    # ----------------------------
    # 直接单位控制
    # ----------------------------
    def move_units(self, actors: Sequence[Actor], location: Location, attack_move: bool = False) -> None:
        """移动单位到指定位置。

        Args:
            actors (Sequence[Actor]): 要移动的单位列表。
            location (Location): 目标位置。
            attack_move (bool): 是否攻击移动（遇敌自动交战）。
        """
        self.api.move_units_by_location(list(actors), location, attack_move=attack_move)

    def attack_move(self, actors: Sequence[Actor], location: Location) -> None:
        """攻击移动到指定位置（遇敌自动交战）。

        Args:
            actors (Sequence[Actor]): 要移动的单位列表。
            location (Location): 目标位置。
        """
        self.api.move_units_by_location(list(actors), location, attack_move=True)

    def attack_target(self, attacker: Actor, target: Actor) -> bool:
        """指定单位攻击指定目标。

        Args:
            attacker (Actor): 发起攻击的单位。
            target (Actor): 被攻击的目标。

        Returns:
            bool: 是否成功发起攻击。
        """
        return self.api.attack_target(attacker, target)

    def stop_units(self, actors: Sequence[Actor]) -> None:
        """停止单位当前行动。

        Args:
            actors (Sequence[Actor]): 要停止的单位列表。
        """
        self.api.stop(list(actors))

    def repair(self, actors: Sequence[Actor]) -> None:
        """修理建筑或载具（建筑直接修理，载具需要维修中心）。

        Args:
            actors (Sequence[Actor]): 要修理的建筑或载具列表。
        """
        self.api.repair_units(list(actors))

    def set_rally_point(self, buildings: Sequence[Actor], location: Location) -> None:
        """设置建筑的集结点。

        Args:
            buildings (Sequence[Actor]): 建筑列表。
            location (Location): 集结点位置。
        """
        self.api.set_rally_point(list(buildings), location)

    # ----------------------------
    # 信息查询
    # ----------------------------
    def player_base_info(self):
        """查询玩家基础信息（现金、资源、电力）。

        Returns:
            PlayerBaseInfo: 含 Cash/Resources/Power/PowerDrained/PowerProvided。
        """
        return self.api.player_base_info_query()

    # ----------------------------
    # 兼容：把旧 SkillResult 风格方法标记为弃用（但不再提供）
    # ----------------------------


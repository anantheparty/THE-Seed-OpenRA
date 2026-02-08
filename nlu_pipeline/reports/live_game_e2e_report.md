# Live Game E2E Report

- result: FAIL
- baseline: power=0 barracks=0 infantry=0 all_my=1
- final: power=1 barracks=1 infantry=1 all_my=4

## Checks
- PASS: safe_query_routed | {'agent': 'human', 'command': '查看己方步兵', 'source': 'nlu_route', 'reason': 'safe_intent_routed', 'intent': 'query_actor', 'confidence': 0.9999929351456538, 'route_intent': 'query_actor', 'matched': True, 'risk_level': 'low', 'latency_ms': 11.645725928246975, 'rollout_allowed': True, 'rollout_reason': 'rollout_full_percentage', 'execution_success': True, 'timestamp': 1770487009732}
- FAIL: safe_composite_routed | {'agent': 'human', 'command': '先查看己方步兵然后查看敌方步兵', 'source': 'llm_fallback', 'reason': 'composite_low_router_score', 'intent': 'composite_sequence', 'confidence': 0.9999999997977318, 'route_intent': 'composite_sequence', 'matched': True, 'risk_level': 'high', 'latency_ms': 5716.97129542008, 'rollout_allowed': True, 'rollout_reason': 'rollout_full_percentage', 'execution_success': True, 'timestamp': 1770487028736}
- PASS: blocked_command_fallback | {'agent': 'human', 'command': '打开设置', 'source': 'llm_fallback', 'reason': 'blocked_by_safety_pattern', 'intent': None, 'confidence': 0.0, 'route_intent': None, 'matched': False, 'risk_level': 'low', 'latency_ms': 3266.1851439625025, 'rollout_allowed': True, 'rollout_reason': 'rollout_not_checked', 'execution_success': False, 'timestamp': 1770487034065}
- PASS: attack_command_routed_or_guarded | {'agent': 'human', 'command': '用步兵攻击敌方矿车', 'source': 'nlu_route', 'reason': 'attack_gated_routed', 'intent': 'attack', 'confidence': 0.9999999642490367, 'route_intent': 'attack', 'matched': True, 'risk_level': 'high', 'latency_ms': 15.890415757894516, 'rollout_allowed': True, 'rollout_reason': 'rollout_full_percentage', 'execution_success': False, 'timestamp': 1770487029776}
- PASS: powerplant_non_decrease | {'base_power': 0, 'final_power': 1}
- PASS: barracks_non_decrease | {'base_barracks': 0, 'final_barracks': 1}
- PASS: infantry_non_decrease | {'base_infantry': 0, 'final_infantry': 1}

## Command Results
- 查看己方步兵 | nlu=nlu_route/safe_intent_routed | success=True | msg=查询完成，匹配到0个单位 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "query_actor"\n    actors = api.query_actor(TargetsQueryParam(type=['步兵'], faction='己方', range='all'))\n    logger.info("查询完
- 展开基地车 | nlu=nlu_route/safe_intent_routed | success=True | msg=已展开基地车 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "deploy_mcv"\n    api.deploy_mcv_and_wait(wait_time=1.0)\n    logger.info("基地车已展开")\n    _step_messages.append("已展开基地车")\n  
- 建造一个电厂 | nlu=nlu_route/safe_intent_routed | success=True | msg=已生产1个电厂 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "produce"\n    if not api.ensure_can_produce_unit('电厂'):\n        raise RuntimeError('不能生产电厂：前置不足或失败')\n    api.produce_wait
- 建造一个兵营 | nlu=nlu_route/safe_intent_routed | success=True | msg=已生产1个兵营 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "produce"\n    if not api.ensure_can_produce_unit('兵营'):\n        raise RuntimeError('不能生产兵营：前置不足或失败')\n    api.produce_wait
- 造一个步兵 | nlu=nlu_route/safe_intent_routed | success=True | msg=已生产1个步兵 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "produce"\n    if not api.ensure_can_produce_unit('步兵'):\n        raise RuntimeError('不能生产步兵：前置不足或失败')\n    api.produce_wait
- 先查看己方步兵然后查看敌方步兵 | nlu=llm_fallback/composite_low_router_score | success=True | msg=己方步兵: 1个, 敌方步兵: 0个 | code=try:\n    my_infantry = api.query_actor(TargetsQueryParam(type=["步兵"], faction="自己"))\n    enemy_infantry = api.query_actor(TargetsQueryParam(type=["步兵"], faction="敌人"))\n    \n    my_count = len(my_i
- 用步兵攻击敌方矿车 | nlu=nlu_route/attack_gated_routed | success=False | msg=执行失败: 未找到攻击者 | code=try:\n    _step_messages = []\n    _current_step = 1\n    _current_intent = "attack"\n    attackers = api.query_actor(TargetsQueryParam(type=['步兵'], faction='己方', range='selected'))\n    targets = api
- 打开设置 | nlu=llm_fallback/blocked_by_safety_pattern | success=False | msg=无法在游戏内执行'打开设置'操作 | code=try:\n    # 打开设置不是游戏内操作，无法执行\n    __result__ = {"success": False, "message": "无法在游戏内执行'打开设置'操作", "observations": ""}\nexcept Exception as e:\n    __result__ = {"success": False, "message": f"失败: {e}",

## [2026-04-11 05:49] DONE — 完善领域专家：基于游戏真实数据修正知识库
- 修改了 `OPENING_BUILD_ORDER` 区分盟军和苏军的兵营（tent vs barr）。
- 修改了 `_COUNTER_TABLE` 使得对抗推荐更贴合游戏阵营（盟军用吉普车/中坦，苏军用步枪兵/防空车/重坦）。
- 更新了 `ProductionAdvisor` 接收 `faction` 参数并在推荐克制单位时使用。
- 修复并验证了相关的单元测试 `tests/test_planners.py`。

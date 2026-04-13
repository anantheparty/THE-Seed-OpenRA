# OpenRA / RA1 Expert Knowledge Table

## Purpose

This document collects gameplay knowledge that should be stored in Experts and Planner modules instead of leaving the LLM to infer it from raw world state.

Design principle:

- Prefer positive, actionable knowledge.
- Do not teach the model by listing nonexistent options.
- Expert outputs should carry the recommended recovery/action options directly.
- Separate hard facts from soft strategy:
  - Hard facts: unit/building IDs, prerequisites, queue types, power values, radar capability, low-power trait semantics, shared queue semantics, downstream unlock graph.
  - Soft strategy: opening priorities, scouting priorities, expansion heuristics, when to tech.

## Current State

Current hard knowledge is split across:

- [unit_registry.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/unit_registry.py)
- [openra_api/game_api.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/openra_api/game_api.py)
- [experts/planners.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/planners.py)
- individual Experts such as [experts/economy.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/experts/economy.py)

## Source Buckets

### Hard-Fact Sources

- OpenRA RA rules YAML in this repo:
  - [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml)
  - [vehicles.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/vehicles.yaml)
  - [player.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/player.yaml)
  - [ai.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/ai.yaml)
- OpenCodeAlert Copilot aliases:
  - [Copilot.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/common/Copilot.yaml)
- OpenRA trait docs:
  - https://docs.openra.net/en/release/traits/

### Soft-Strategy Sources

- CnCNet RA1 build-order discussion:
  - https://forums.cncnet.org/topic/2526-red-alert-1-building-order-first-10/
- StrategyWiki RA1 structures/gameplay:
  - https://strategywiki.org/wiki/Command_%26_Conquer%3A_Red_Alert/Allied_structures
  - https://strategywiki.org/wiki/Command_%26_Cnquer%3A_Red_Alert/Gameplay
- Red Alert Internet Strategy Guide:
  - https://www.gamingroom.net/dicas-e-solucoes/the-red-alert-internet-strategy-guide/
- Sharoma Red Alert Logistics:
  - https://sharoma.com/ral/logistics.htm
- Reddit/OpenRA RA player heuristics:
  - https://www.reddit.com/r/commandandconquer/comments/h9z2fh
  - https://www.reddit.com/r/openra/comments/d9qwfr
  - https://www.reddit.com/r/openra/comments/qgjjvc

## Hard Facts Worth Encoding

| Fact | Value | Source |
| --- | --- | --- |
| `POWR` | Small power plant, queue `Building`, prereq `~techlevel.infonly`, provides `anypower`, power `+100` | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml) |
| `APWR` | Advanced power plant, queue `Building`, prereq `dome`, power `+200` | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml) |
| `PROC` | Refinery is a full economy package: queue `Building`, prereq `anypower`, acts as unload dock, provides resource storage `2000`, and spawns a free `HARV` | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml), [vehicles.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/vehicles.yaml) |
| `HARV` | Harvester requires `proc`; it is an economy unit, not a default scout | [vehicles.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/vehicles.yaml) |
| `WEAP` | War Factory requires `proc`; it is the main vehicle production gateway and the bridge from infantry/econ into mobile scouting and armor play | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml) |
| `DOME` | Radar Dome requires `proc`; provides radar/minimap only when not jammed and not disabled, has stronger shroud reveal online and reduced local reveal offline | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml), [player.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/player.yaml) |
| `BARR` / `TENT` | Barracks require `anypower` and unlock infantry queues | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml) |
| `FIX` | Service depot requires `weap`; `MCV` requires `fix` at medium tech | [structures.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/structures.yaml), [vehicles.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/vehicles.yaml) |
| Low power | Two distinct systems exist: queue slowdown via `LowPowerModifier`, and building disable via explicit low-power traits | [player.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/player.yaml), [defaults.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/defaults.yaml) |
| Shared queue | `ClassicProductionQueue` is attached to the player actor, not a building; buildings with `Production` execute output for a shared queue | https://docs.openra.net/en/release/traits/ |
| AI power reserve | OpenRA RA AI keeps excess power margins and explicitly treats `powr,apwr` as power types | [ai.yaml](/Users/kamico/work/theseed/THE-Seed-OpenRA/OpenCodeAlert/mods/ra/rules/ai.yaml) |

## Structural Role Graph

| Node | Roles | Upstream | Downstream unlock significance |
| --- | --- | --- | --- |
| `POWR` | `power_recovery` | none | unlocks `anypower`, which gates early production buildings |
| `PROC` | `economy_anchor`, `expansion_anchor` | `anypower` | enables `HARV`, `WEAP`, `DOME`; provides unload/storage/free harvester |
| `WEAP` | `vehicle_gateway`, `tech_gateway` | `PROC` | enables vehicle production, mobile scouting transition, armor play, and `FIX` |
| `FIX` | `repair_gateway`, `tech_gateway` | `WEAP` | enables `MCV`; repairs vehicles |
| `DOME` | `awareness_gateway`, `tech_gateway` | `PROC` | enables `APWR`, `AGUN`, `AFLD`, `ATEK`, `STEK`; grants radar/minimap |
| `BARR` / `TENT` | `infantry_gateway` | `anypower` | early infantry production, early scouting by riflemen |
| `MCV` | `expansion_anchor` | `FIX` | creates new construction yard / expansion base |

## Low-Power Disable Classes

Do not model low power as one generic blocker. Split it into queue slowdown and structure disable classes.

### Queue / production impact

- Player has `LowPowerModifier` in RA rules.
- This affects production tempo even when a structure is not hard-disabled.

### Building disable classes

| Trait class | Meaning | Example structures |
| --- | --- | --- |
| `^DisableOnLowPower` | auto-disabled on low/critical power | `ATEK` |
| `^DisableOnLowPowerOrPowerDown` | auto-disabled on low/critical power, and also supports explicit player power-down | `DOME`, `TSLA`, `AGUN`, `SAM` |
| `^DisabledByPowerOutage` | disabled only by power-outage condition, not the generic low-power path | `POWR`, `APWR` |

Agent implication:

- `EconomyExpert` should surface `queue_slowed` and `power_recovery` for production issues.
- `ReconExpert` should surface `awareness_degraded` if radar infrastructure is disabled.
- `CombatExpert` should surface `defense_offline` if low-power-disabled defenses matter to the current engagement.

## Shared Queue Semantics

OpenRA classic production queues are player-scoped shared queues, not building-local queues.

Implications for the agent system:

- `ready_item_pending` is a player-queue blockage, not just one building's local state.
- “clear ready building” is a queue-unblock recovery action.
- primary-building selection matters for where produced units exit.
- multiple production buildings can accelerate throughput for the same shared queue.
- queue reasoning belongs partly in `EconomyExpert`, but the underlying fact must be treated as player-state, not actor-state.

## Knowledge To Move Into Experts

| ID | Knowledge | Owner | Priority | Encode As | Source Basis |
| --- | --- | --- | --- | --- | --- |
| E1 | Low power is a production blocker. Recovery should recommend currently buildable power structures, not generic free-form advice. | `EconomyExpert` | P0 | `BLOCKED(reason="low_power", recommendation={kind:"power_recovery", options:[...]})` | OpenRA YAML power/prereq facts |
| E2 | Queue ready item pending means the build queue is blocked by an already-finished building waiting for placement. | `EconomyExpert` | P0 | `BLOCKED(reason="queue_ready_item_pending", recommendation={kind:"clear_ready_building"})` | Current runtime behavior |
| E3 | A building task should complete when the structure lands, even if low power begins immediately after. | `EconomyExpert` | P0 | completion-before-block ordering | Live bug from Radar Dome test |
| E4 | Refinery is a full economy package: income anchor + unload dock + storage + free harvester. | `EconomyExpert` | P0 | `roles=["economy_anchor","expansion_anchor"]`, plus recovery package metadata | YAML + RA strategy guides |
| E5 | When credits are low, extra refinery / harvester recovery is a valid economic remedy, and a second refinery is often better than a second ore truck. | `EconomyExpert` + `ProductionAdvisor` | P1 | `recommendation.options=[proc,harv]` and weighted econ heuristic | CnCNet/Sharoma/community guides |
| E6 | Barracks are infantry production infrastructure, not generic tech progression. | `EconomyExpert` | P1 | building-role metadata | YAML prereqs/production |
| E7 | Radar Dome is scouting/awareness infrastructure and also a mid-tech gateway with explicit downstream unlocks (`APWR`, `AGUN`, `AFLD`, `ATEK`/`STEK`). | `EconomyExpert` + `ReconExpert` + `Planner` | P0 | building-role metadata `roles=["awareness_gateway","tech_gateway"]` with unlock graph | YAML `ProvidesRadar`, prereqs, shroud reveal |
| E8 | Losing radar should be surfaced as “awareness degraded”, not just a generic structure loss. | `ReconExpert` + `Planner` | P1 | `BLOCKED(reason="radar_missing", impact="reduced_awareness")` | YAML radar capability |
| E9 | Scouting priority should be staged: early few riflemen for initial contact, then cheap fast vehicle for deeper recon; harvesters should never be default scouts. | `ReconExpert` | P0 | staged scout policy | OpenRA/RA community scouting heuristics |
| E10 | Base scouting with no target found should close as `partial` with exploration delta, not run forever. | `ReconExpert` | P0 | `TASK_COMPLETE(result="partial", data={explored_pct_delta,...})` | Round 7 live fix |
| E11 | “Attack with no visible enemy” should not silently drift into unrelated economic actions. | `CombatExpert` + `Planner` | P0 | `BLOCKED(reason="no_visible_target", recommendation={kind:"recon_first"})` | Live drift observed |
| E12 | Build placement should favor ore-adjacent refinery placement and sensible base stretch; this is map/econ knowledge, not LLM improvisation. | `EconomyExpert` + future placement planner | P1 | placement policy / score function | RA strategy guides and player heuristics |
| E13 | War Factory is the main vehicle gateway and must be treated as a first-class decision point between “stay econ/infantry” and “transition into mobile scouting/armor”. | `EconomyExpert` + `ProductionAdvisor` | P0 | `roles=["vehicle_gateway","tech_gateway"]` | YAML prereqs/production |
| E14 | Openings should be soft templates, not rigid scripts. Standard early pattern is power -> barracks -> refinery -> war factory, then adapt to map and plan. | `ProductionAdvisor` | P1 | weighted opening template, not a hard sequence | CnCNet/community strategy sources |
| E15 | Teching up should consider defense/econ cover, not just “build higher-tier structure now”. | `ProductionAdvisor` | P1 | precondition checklist before tech recommendation | CnCNet discussion |
| E16 | If a harvester is lost and economy must continue, refinery/harvester recovery should be explicit advice. | `ProductionAdvisor` | P1 | `recommendation.kind="econ_recovery"` | CnCNet discussion, RA guides |
| E17 | Power advice must be faction/game-specific and availability-aware. Present what is buildable now, not generic C&C lore. | All Experts that mention recovery | P0 | registry-backed option rendering | User feedback + YAML |
| E18 | Shared queue semantics must be explicit in expert reasoning and payloads; queue blockage is player-scoped. | `EconomyExpert` + `Planner` | P0 | `queue_scope="player_shared"` metadata | OpenRA trait docs |

## Recommended Signal Shape

### Example: low power

```json
{
  "kind": "blocked",
  "summary": "电力不足，建议补建发电厂或高级发电厂",
  "expert_state": {
    "phase": "waiting",
    "reason": "low_power",
    "queue_type": "Building"
  },
  "data": {
    "reason": "low_power",
    "queue_type": "Building",
    "recommendation": {
      "kind": "power_recovery",
      "options": [
        {"unit_type": "powr", "display_name": "发电厂"},
        {"unit_type": "apwr", "display_name": "高级发电厂"}
      ]
    }
  }
}
```

### Example: attack with no target

```json
{
  "kind": "blocked",
  "summary": "当前没有可见敌方目标，建议先执行侦察",
  "data": {
    "reason": "no_visible_target",
    "recommendation": {
      "kind": "recon_first",
      "expert_type": "ReconExpert",
      "config_hint": {
        "search_region": "enemy_half",
        "target_type": "base",
        "target_owner": "enemy"
      }
    }
  }
}
```

## Guidance For Implementation

### What should stay in Experts

- Immediate blocker interpretation
- Recovery recommendations tied to concrete game facts
- Unit/building role knowledge
- Queue semantics
- Resource-role semantics
- Downstream unlock graph for infrastructure decisions

### What should stay in Planner

- High-level sequencing
- Opening adaptation by map/economy state
- Transition rules:
  - econ -> tech
  - tech -> recon
  - recon -> attack

### What should not stay in raw prompt

- Exact buildable power options
- Structure-role facts
- Queue blockage semantics
- “What to do when radar/power/refinery is missing”

## Historical First Fill Order (Reference Only)

1. `EconomyExpert`
   - low power recovery
   - queue blocked / ready item blocked
   - refinery/harvester recovery
   - radar as awareness-tech building
   - war factory as vehicle gateway
   - shared queue scope metadata
2. `ReconExpert`
   - staged scout unit preference
   - radar impact on map awareness
   - no-target recon closure and reporting
3. `CombatExpert`
   - no-visible-target fallback
   - defend/hold/harass recommendation boundaries
4. `ProductionAdvisor`
   - opening templates
   - power/econ/tech trade-off heuristics
   - proc -> weap vs proc -> dome transition heuristics

## Review Questions

1. Should Radar Dome be modeled primarily as `scouting`, `tech`, or both?
2. Should power recovery advice mention only currently buildable options, or also near-term next-tier options?
3. Should refinery placement knowledge live in `EconomyExpert`, or in a separate future `PlacementPlanner`?
4. Do we want a single `knowledge/` directory with YAML, or keep the first version inside Experts as Python rules?

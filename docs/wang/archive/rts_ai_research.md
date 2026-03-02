# RTS AI Research for Expert System Design

Date: 2026-03-29
Author: yu

Goal: extract traditional RTS AI techniques that are directly useful for designing OpenRA expert systems, especially for nontrivial tactics like surround, pincer, retreat, and coordinated timing.

## Executive summary

The strongest lesson from competitive RTS bot history is not "pick one AI formalism." It is:

- decompose by scale: strategy -> operations/tactics -> micro
- keep each layer specialized
- use spatial representations aggressively
- use reactive controllers for fast adaptation
- use planning/search selectively where it buys coordination

The public StarCraft bot literature does **not** show a consensus around a single winner such as BT, FSM, GOAP, or HTN. The winning pattern is usually a **hybrid hierarchy**:

- manager/module architecture at the top
- squads / task groups in the middle
- specialized reactive micro controllers at the bottom
- blackboard/message passing/shared world state between them

For OpenRA, the most important implication is this:

- "surround" should not be a coordinate offset trick
- it should be a coordinated tactical method driven by terrain analysis, local threat maps, role assignment, and timing windows

## 1. Influence maps / threat maps

### What they are used for in RTS bots

Influence maps are spatial fields that encode danger, visibility, mobility, value, or strategic desirability over the map. In StarCraft bots, they are most useful when they are **not** treated as a monolithic single map. The better pattern is layered maps:

- static terrain / chokepoint / corridor layer
- enemy vision layer
- enemy weapon threat layer
- friendly support / reinforcement layer
- objective or route desirability layer

This is exactly the kind of substrate needed for real tactics.

### What public BWAPI sources show

#### UAlbertaBot line

Public UAlbertaBot descriptions emphasize:

- real-time build-order search
- combat simulation (`SparCraft`)
- squads and combat commander

UAlbertaBot itself is best known for squad control plus combat simulation, not as an influence-map-centric bot core. But influence maps were later layered onto UAlbertaBot-derived work.

The clearest public example is Lucas Critch's MSc thesis on sneak attacks built on UAlbertaBot:

- a **common-path influence map** is built once at game start from likely enemy travel paths between bases
- an **enemy vision influence map** is recalculated every frame from currently visible enemy positions
- pathfinding then uses A* plus the influence costs to choose safer drop routes

This is important because it shows a practical pattern:

- precompute static influence where possible
- update only highly dynamic layers every frame
- use influence to bias search, not replace it

Applicability to OpenRA:

- precompute likely attack corridors from bases to expansions / fronts
- maintain a live enemy-vision and enemy-firepower field
- choose flank routes that minimize detection and concentrated anti-unit coverage

#### Steamhammer

Steamhammer is a UAlbertaBot fork and still uses the same broad module vocabulary:

- `GameCommander`
- `CombatCommander`
- `Squad`
- tile/walk-tile `Grid` classes
- `InformationManager`

Public docs show heavy use of:

- squads
- combat simulation
- tile-level grid data
- opponent modeling
- pathfinding / map analysis

I did **not** find strong public evidence that Steamhammer's core tactics are built around explicit dense influence maps in the same way as PurpleWave or the sneak-attack UAlbertaBot research extension. What is clearly documented is:

- squad-level tactical control via `CombatCommander`
- unit-level smoothing/hysteresis to reduce oscillation
- specialized micro controllers such as `MicroDefilers`
- grid-based information storage at tile and walk-tile resolution

So for Steamhammer the useful takeaway is:

- influence/grid representations are part of the substrate
- actual tactical behavior is still primarily organized around squads, combat sim, and specialized micro managers

#### PurpleWave

PurpleWave is not one of the exact bots Wang named, but it is relevant because the public source links are explicit:

- per-unit fast influence grids
- enemy vision grid
- influence-aware pathfinding for drops

This gives a stronger concrete reference implementation for "how to do it in practice" than many bot descriptions do.

#### Iron

Iron's public docs stress something different:

- decentralized multi-agent system
- highly autonomous unit agents
- 25 behaviors per unit
- hybrid with some global strategies and algorithms
- strong dependence on BWEM terrain analysis

I did **not** find public evidence that Iron's identity is influence-map-centric. Public descriptions point more toward:

- autonomous agents
- terrain knowledge
- per-unit behavior switching
- robustness against indecision

That matters because it shows another successful path: not all strong RTS bots lean on influence maps as the primary tactical abstraction.

### Efficient computation/update patterns

The best recurring pattern from the sources is:

1. Separate static from dynamic layers.
2. Recompute only the dynamic layers frequently.
3. Use coarse grids for global reasoning and local sampling for control.

Concrete techniques:

- Build static maps once:
  - chokepoints
  - base-to-base corridors
  - terrain bottlenecks
  - common scout/attack paths
- Recompute dynamic maps often:
  - enemy vision
  - current enemy firepower
  - local reinforcement density
  - retreat safety
- Do not always evaluate the full map:
  - evaluate only path candidates
  - evaluate only local neighborhoods for potential fields
  - reuse flood-fill / propagation structures

The Opprimo/Hagelbäck style hybrid is especially practical:

- use normal/global pathfinding when no enemy is nearby
- switch to local potential-field or influence-aware motion when combat context appears

### How they inform tactical decisions

Influence maps are most valuable when they change *where* and *when* a tactic executes:

- **surround**
  - choose flank arcs with walkability and low anti-armor/anti-infantry pressure
  - avoid sending a flank group through a narrow kill corridor
- **retreat**
  - pick routes to safety, not just shortest routes home
- **runby / sneak attack**
  - favor low-vision and low-interception routes
- **engage / disengage**
  - estimate whether local support fields are improving or worsening
- **staging**
  - find rendezvous points outside enemy vision but within fast reinforcement distance

### OpenRA-specific recommendation

OpenRA experts should probably maintain at least these four maps:

1. `terrain_mobility_map`
   - passability, chokepoints, ramps/corridors
2. `enemy_vision_map`
   - currently observed / likely seen tiles
3. `enemy_threat_map`
   - weapon-weighted threat by unit class
4. `friendly_support_map`
   - reinforcement density, retreat cover, local anti-air/anti-armor

Then "surround" becomes:

- select 2-3 approach sectors
- assign roles by unit type
- compute synchronized staging positions
- launch only when threat gradients and ETA windows align

## 2. Behavior trees vs FSM vs GOAP vs HTN for RTS

### What top RTS bot authors actually use

The short answer is: **hybrids and custom architectures**.

#### UAlbertaBot / Steamhammer family

The UAlbertaBot family is built around:

- managers/commanders
- squads
- combat simulation
- build-order planning/search

This is neither pure BT nor pure FSM nor GOAP nor HTN.

#### EISBot

EISBot is the clearest behavior-tree-adjacent example in Brood War bot history, though its actual implementation is ABL (A Behavior Language), a reactive planning language rather than a conventional game-industry BT.

EISBot uses:

- daemon behaviors
- managers
- message passing
- working memory / blackboard
- unit subtasks

Its key insight is not "BTs are best." The key insight is:

- hierarchical concurrent reactive behaviors
- compartmentalized managers
- blackboard/message passing across layers

#### Iron

Iron uses:

- decentralized multi-agent control
- highly autonomous unit agents
- many per-unit behaviors
- some global strategies and algorithms

This is closer to agent-based behavior switching than to monolithic BT/GOAP/HTN.

#### Search/planning-focused tactical systems

Separate tactical planners exist as libraries or modules:

- `StarAlgo` for squad movement with `MCTSCD` and `Negamax`
- research bots such as LetaBot/Nova using MCTS for squad movement
- HTN/AHTN-R work in RTS planning research

This suggests that search/planning is often bolted onto a larger architecture rather than used as the only behavior formalism.

### BT vs FSM vs GOAP vs HTN in practice

#### FSM

Strengths:

- simple
- predictable
- good for tight reactive squad states
- easy to debug

Weaknesses:

- transitions explode with complexity
- weak compositionality
- awkward for concurrency

Best fit in RTS:

- unit micro modes
- squad engagement modes
- retreat/hold/advance/harass states

#### Behavior Trees / reactive planners

Strengths:

- modular
- reactive
- good for concurrency and fallback
- easier to decompose by competence areas

Weaknesses:

- can become implicit spaghetti if blackboard use is uncontrolled
- low-level continuous control still has to live elsewhere
- large trees need discipline to remain understandable

Best fit in RTS:

- task-layer execution logic
- subsystem orchestration
- concurrent monitoring + action

#### GOAP

GOAP is attractive conceptually, but I found little evidence that strong StarCraft bots rely on it as their core architecture.

Interpretation from the sources:

- the competitive bot scene tends to prefer custom hierarchical managers, reactive control, search, and combat simulation
- GOAP appears more common in general game AI discussion than in top SC bot implementations

Best fit if used:

- high-level goal selection with small action spaces
- not frame-level squad micro

#### HTN

HTN is more realistic for RTS than GOAP because it matches hierarchical task decomposition better. But the public evidence again suggests:

- strong in research
- less common as the sole practical control architecture in top bots

The AHTN-R work is valuable because it adds:

- phases
- exit conditions
- task repair after failure

This is directly relevant to any future OpenRA task system.

### Is there a consensus best approach for squad-level AI?

No single consensus best formalism.

The practical consensus is closer to:

- **FSM or small reactive controller** for the squad's immediate posture
- **BT/reactive planner/manager system** for coordination and fallback
- **search/planning** for movement/path/timing when the tactical problem is spatially hard

For OpenRA squad AI, my conclusion is:

- use a lightweight squad FSM or state tree for immediate posture
- feed it with world-model queries and tactical planners
- use search or route optimization only when needed

In other words: do **not** pick one architecture and force everything into it.

## 3. Tactical group / squad AI

### How real RTS AIs coordinate multi-unit tactics

The public sources point to several recurring mechanisms:

- squads/groups as a first-class tactical unit
- local combat evaluation (combat simulation or learned evaluation)
- per-unit micro controllers under squad authority
- blackboard/message passing for coordination
- route planners for group movement
- hysteresis/smoothing to reduce twitching

Concrete examples:

- UAlbertaBot / Steamhammer: `CombatCommander` assigns squads and uses combat sim
- Steamhammer: cluster-level combat sim smoothing to avoid oscillation
- EISBot: Tactics Manager forms squads; damaged dragoons temporarily leave squad control via unit subtask and later rejoin
- StarAlgo: explicitly plans coordinated squad movement with MCTSCD or Negamax

### What state machines drive squad behavior

Public bot documentation often does not expose a textbook FSM diagram, but the implicit states are clear:

- gather / stage
- move / reposition
- engage
- retreat / regroup
- harass / runby / drop
- hold defense

This is exactly where FSMs remain useful:

- small number of mutually exclusive postures
- fast transitions
- explicit and debuggable

### How timing coordination is handled

Timing coordination is one of the least glamorous but most important parts.

Observed techniques:

- wait until required squads are full before attack (OpprimoBot style squad requirements)
- use daemon/concurrent behaviors so multiple preparations happen in parallel (EISBot)
- stage at waypoints so squads do not string out (OpprimoBot)
- use hysteresis/smoothing to avoid frame-to-frame oscillation (Steamhammer)
- use search/planning at squad level for coordinated movement (StarAlgo)

### What this means for "surround"

A real surround expert probably needs:

1. **Approach analysis**
   - extract reachable sectors around the enemy
   - reject sectors blocked by terrain or high threat
2. **Role assignment**
   - fixers/front screen
   - flankers
   - ranged damage group
   - anti-escape or anti-reinforcement group
3. **Timing model**
   - estimate ETA for each sub-group
   - wait until arrival window overlaps
   - optionally trigger feint first, main collapse second
4. **Adaptation**
   - if enemy rotates early, convert surround to chase cut-off or collapse on one side
   - if one flank route becomes too dangerous, abort to pincer or retreat

So "surround" should be a **tactical method** with phases, not a single pathing command.

### OpenRA-specific recommendation

Make squad tactics experts around reusable methods:

- `HoldChoke`
- `PincerAttack`
- `EncircleTarget`
- `FightingRetreat`
- `RunbyHarass`
- `EscortSiege`

Each method should:

- query terrain/threat maps
- compute sub-group roles
- expose phase state
- adapt or fail over when assumptions break

## 4. Potential fields for micro

### What the classic sources show

The classic StarCraft lesson is not "potential fields solve everything." It is:

- potential fields are very good for **local**, **reactive**, **continuous** unit control
- they are weak as a full-map planner in terrain-heavy RTS maps

That conclusion appears repeatedly.

#### Hagelbäck / OpprimoBot

Key pattern:

- A* for long-range movement when no nearby enemy exists
- switch to potential fields when enemies/buildings enter local context

This hybrid outperformed pure A* in the cited experiments and pure potential-field navigation alone struggled on full StarCraft terrain because of chokepoints and local minima.

Important detail:

- the hybrid gained local behaviors like surrounding enemy units

#### ICEBot potential flow work

The potential flow paper is useful because it explicitly targets combat positioning, not just navigation:

- impose a uniform flow toward the target
- add repulsive sources at enemy units
- move along streamlines
- the resulting geometry produces a natural surrounding effect

That is directly relevant to the user's complaint about fake surround.

### Beyond simple PF

The more advanced lesson is to separate:

- **global route choice**
- **local force/motion control**

Good combinations include:

- A* + potential fields
- influence maps + heuristic search
- squad route search + per-unit local fields
- search-derived staging + local stream/force micro

Potential fields alone are not enough for:

- route timing through chokepoints
- deciding whether to flank at all
- coordinating multi-group arrival

### How competitive SC2 bots handle micro

Public SC2 full-game research has moved more toward:

- modular RL
- hierarchical RL
- specialized combat models
- multi-agent role assignment

than toward explicitly published potential-field systems.

The important architectural point remains the same:

- high-level modules choose intent
- lower-level systems handle execution details quickly

Examples:

- modular SC2 architecture with a centralized scheduler over specialized modules
- hierarchical RL with extracted macro-actions and separate combat models
- TStarBot-X with multi-agent roles and rule-guided policy search

So even in newer SC2 work, the overall pattern is still hierarchical and specialized rather than end-to-end flat control.

### OpenRA-specific recommendation

BiodsEnhancer-style local force methods are useful, but they should sit **under** a tactical method layer.

For OpenRA I would treat potential fields/flow as:

- local micro primitive
- formation / spacing primitive
- collision / standoff / encirclement primitive

and not as the whole tactical system.

## 5. Strategic layer architecture

### Common structure in strong RTS AIs

The standard decomposition is:

1. **Strategy**
   - build order
   - tech switches
   - expansion timing
   - attack/defense posture
2. **Tactics / operations**
   - squad assignments
   - area control
   - target selection at group level
   - route planning
3. **Micro / execution**
   - kiting
   - focus fire
   - formation spacing
   - flee/reengage behavior

The competition survey states this explicitly: most current bots decompose the game into a hierarchy of smaller subproblems such as higher-level strategy, tactics, combat control, terrain analysis, and intelligence gathering.

### Communication patterns between layers

The sources show several recurring patterns.

#### Blackboard / shared memory

Examples:

- EISBot working memory / blackboard
- CherryPi blackboard with key-value pairs and UPC objects

Good for:

- decoupling
- asynchronous producers/consumers
- mixing learned and hand-authored modules

#### Message passing / requests

Example:

- EISBot managers write requests and other managers consume them

Good for:

- delegating execution without tightly coupling modules
- coordinating resource use and locking

#### Command objects / macro requests

Examples:

- CherryPi UPCs
- modular SC2 architecture macros proposed to centralized scheduler

Good for:

- arbitration
- unified logging / replay analysis
- mixing planners and executors

#### Shared task groups / squads

Examples:

- UAlbertaBot / Steamhammer squads
- EISBot tactics manager + unit subtasks
- StarAlgo squad movement planning

Good for:

- localized coordination
- timing
- clear ownership of tactical intent

### Best architectural lesson for OpenRA

Use a shared world model plus message/command passing:

- strategy creates or updates tasks / directives
- tactical experts convert them into squad methods and staging plans
- micro experts execute continuously with local reactivity
- kernel arbitrates only when conflicts happen

This lines up well with Wang's latest constraint:

- passive kernel
- tasks own persistent loops

## 6. LLM + traditional AI hybrid

### Existing work found

#### SwarmBrain (2024)

Most directly relevant hybrid paper I found.

Architecture:

- LLM-based macro "Overmind Intelligence Matrix"
- fast "Swarm ReflexNet" for tactical responses
- reflex layer uses a condition-response state machine because LLM latency is too high for low-level control

This is almost exactly the split we have been discussing.

#### Adaptive Command (2025)

Architecture:

- LLM strategic advisor
- behavior tree for action execution
- natural language interface

This is especially relevant because it explicitly pairs language reasoning with a traditional execution structure rather than replacing it.

#### TacticCraft (2025)

Promising direction:

- natural-language-driven tactical adaptation for StarCraft II

Even from the abstract alone, it fits the same pattern:

- language conditions policy
- traditional agent executes adapted tactics

### What the hybrid literature suggests

Across the papers I found, the same pattern repeats:

- LLMs are best used for intent interpretation, strategic advice, adaptation, explanation, and task selection
- traditional controllers remain necessary for low-latency execution
- the fast execution layer is usually BT/FSM/rule/micro-controller based

### Applicability to OpenRA

This strongly supports the architecture direction:

- LLM handles user intent, strategic reframing, and occasional high-level replanning
- traditional experts own persistent execution
- world model and tactical methods carry the real game knowledge

In other words, if OpenRA wants believable tactics like surround, the LLM should **name/choose** the tactic, but the **method** itself should be implemented traditionally.

## Design implications for OpenRA expert systems

### 1. Experts should be organized by tactical method, not by user phrase

Good expert boundaries:

- Recon / Scouting
- Area Control
- Mobility / Route Planning
- Squad Tactics
- Fire Control / Micro
- Economy / Production
- Defense / Emergency Response

Bad boundary:

- one expert for "surround"
- one expert for "harass"
- one expert for "retreat"

Those should be reusable tactical methods inside a domain expert.

### 2. World model needs more than unit lists

At minimum it should support:

- zones / sectors
- mobility graph and chokepoints
- enemy vision estimates
- enemy threat fields by unit class
- friendly support fields
- actor availability after reservations
- task ownership / resource bindings

### 3. Tactical methods need explicit phase state

For example `EncircleTarget`:

- `recon`
- `sector_selection`
- `staging`
- `collapse`
- `containment`
- `abort_or_convert`

Without phase state, adaptation will be ad hoc and brittle.

### 4. Timing needs to be modeled explicitly

Surround/pincer/feint tactics require:

- ETA estimates
- synchronization windows
- readiness thresholds
- cancellation rules

### 5. Micro should combine local reactive control with tactical constraints

Micro controller inputs:

- local threat/support fields
- squad role
- tactic phase
- formation anchor
- retreat/standoff policies

That is better than a pure potential field with no tactical context.

## My recommended stack for our redesign

### Strategy layer

Responsibilities:

- choose major objective
- set constraints
- request tactical methods
- choose reinforcements / resource priority

Possible implementation:

- rule/planner/LLM hybrid

### Tactical layer

Responsibilities:

- choose routes and approach sectors
- split forces by role
- compute timing
- monitor method success/failure

Possible implementation:

- method library
- squad FSM/state tree
- optional search for route/sector/timing problems

### Micro layer

Responsibilities:

- local movement and spacing
- fire control
- flee/reengage
- collision and formation handling

Possible implementation:

- potential fields / potential flow
- local heuristics
- specialized per-unit-type controllers

## Direct answers to Wang's 6 prompts

### 1. Influence maps / threat maps

- Strongly useful, but best as layered fields, not one giant map.
- Public examples show them used for sneak-attack routing, enemy vision avoidance, local tactical biasing, and safe-path selection.
- Efficient pattern: static layers precomputed, dynamic layers updated frequently, local evaluation for motion.
- Best OpenRA use: route choice, flank selection, retreat, staging, and threat-aware encirclement.

### 2. BT vs FSM vs GOAP vs HTN for RTS

- Top bot authors mostly use hybrids.
- FSM: best for small reactive posture states.
- BT/reactive planner: best for modular concurrent execution.
- GOAP: little evidence of dominance in top StarCraft bots.
- HTN: valuable for high-level decomposition and repair, more common in research than top bot practice.
- No single consensus winner; hybrid hierarchy is the practical winner.

### 3. Tactical group / squad AI

- Strong bots use squads/groups, combat evaluation, route planning, and reactive micro beneath squad intent.
- Timing is handled through staging, readiness checks, concurrent preparation, and hysteresis.
- Surround/pincer requires sector choice, role assignment, synchronization, and adaptation.

### 4. Potential fields for micro

- Very good for local control.
- Weak as stand-alone global planning on RTS terrain.
- Best used in hybrid with A* / search / squad plans.
- OpenRA should use them below the tactical layer, not as the tactical layer.

### 5. Strategic layer architecture

- Standard hierarchy is strategy -> tactics -> micro.
- Common communication patterns are blackboards, request queues, command objects, and squad ownership.
- A passive kernel plus persistent task loops fits this tradition if the world model is strong enough.

### 6. LLM + traditional AI hybrid

- Existing work already converges on the same split:
  - LLM for macro reasoning / adaptation / NL interaction
  - traditional controller for execution
- This is the right direction for OpenRA.

## Sources

Traditional RTS bot architecture and tactics:

- Certicky, et al. "StarCraft AI Competitions, Bots and Tournament Managers" (2018): https://certicky.github.io/files/publications/Starcraft-AI-ToG-2018.pdf
- CommandCenter README (architecture based on UAlbertaBot): https://github.com/davechurchill/commandcenter
- Steamhammer overview: https://www.satirist.org/ai/starcraft/steamhammer/
- Steamhammer 2.4 orientation: https://satirist.org/ai/starcraft/steamhammer/2.4/
- Steamhammer combat sim smoothing: https://satirist.org/ai/starcraft/blog/archives/1073-Steamhammers-combat-sim-smoothing.html
- Steamhammer defiler control notes: https://satirist.org/ai/starcraft/blog/archives/662-Steamhammer-2.0-defilers.html
- Iron overview: https://bwem.sourceforge.net/Iron.html
- EISBot wiki: https://www.starcraftai.com/wiki/EISBot
- Reactive Planning Idioms for Multi-Scale Game AI: https://alumni.soe.ucsc.edu/~bweber/pubs/weber_cig2010.pdf
- Extending Behavior Trees (Ben Weber): https://www.gamedeveloper.com/business/extending-behavior-trees
- ABL versus Behavior Trees (Ben Weber): https://www.gamedeveloper.com/programming/abl-versus-behavior-trees

Influence maps, tactical pathing, and potential fields:

- Lucas Critch MSc thesis, "Using Influence Maps with Heuristic Search to Craft Sneak-Attacks in StarCraft": https://www.cs.mun.ca/~dchurchill/publications/pdf/theses/LucasCritch_Thesis_MSc.pdf
- "Sneak-Attacks in StarCraft using Influence Maps" (CoG 2021): https://ieee-cog.org/2021/assets/papers/paper_112.pdf
- Hagelbäck, "Potential-field based navigation in StarCraft": https://www.readkong.com/page/potential-field-based-navigation-in-starcraft-3570734
- OpprimoBot documentation: https://doczz.net/doc/4790030/opprimobot---aiguy.org
- Satirist summary of potential fields and PurpleWave links: https://satirist.org/ai/starcraft/blog/archives/65-pathing-5-potential-fields.html
- Nguyen et al., "Potential Flow for Unit Positioning During Combat in StarCraft": https://www.ice.ci.ritsumei.ac.jp/~ruck/PAP/gcce13-tung.pdf

Planning/search/formal methods:

- StarAlgo squad movement planning: https://arxiv.org/abs/1812.11371
- Modified Adversarial HTN Planning in RTS Games (AHTN-R): https://www.mdpi.com/2076-3417/7/9/872
- Continual Online Evolutionary Planning in UAlbertaBot: https://pure.itu.dk/da/publications/continual-online-evolutionary-planning-for-in-game-build-order-ad/

SC2 strategic architectures:

- Modular Architecture for StarCraft II with Deep Reinforcement Learning: https://arxiv.org/abs/1811.03555
- On Efficient Reinforcement Learning for Full-length Game of StarCraft II: https://arxiv.org/abs/2209.11553
- TStarBot-X: https://arxiv.org/abs/2011.13729
- AlphaStar overview: https://deepmind.google/blog/alphastar-mastering-the-real-time-strategy-game-starcraft-ii/

LLM + traditional AI hybrid:

- SwarmBrain: https://arxiv.org/abs/2401.17749
- Adaptive Command: https://arxiv.org/abs/2508.16580
- TacticCraft: https://arxiv.org/abs/2507.15618


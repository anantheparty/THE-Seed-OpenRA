# NLU / Runtime Investigation

Date: 2026-04-02

## 1. OpenCodeAlert console spam root cause

- `OpenCodeAlert/start.sh` defaults to `Game.CopilotDebug=True`.
- `game_control.py` also defaulted `OPENRA_COPILOT_DEBUG` to `True`.
- Therefore normal starts entered Copilot debug mode unless the caller explicitly overrode it.
- This explains why OpenCodeAlert kept printing normal request/response traffic and felt slow.

Current corrective direction:

- Default `CopilotDebug` to `False` in the launch path.
- Keep request/response dumps only for explicit debugging.

## 2. Current live runtime NLU path

The live runtime does **not** use the old `Phase2NLUGateway`.

Current path:

1. frontend `command_submit`
2. `main.py` -> `RuntimeBridge.on_command_submit`
3. `Adjutant.handle_player_input(...)`
4. `Adjutant._try_rule_match(...)`
5. if no rule hit: Adjutant LLM classification
6. managed task -> TaskAgent LLM planning

Important consequence:

- the current runtime does **not** expose old NLU metadata like:
  - `intent`
  - `confidence`
  - `route_intent`
  - `risk_level`
  - `execution_success`

## 3. Why current runtime feels “like untrained LLM”

Because the deterministic front half is much thinner than the old NLU.

Observed gaps:

- `_try_rule_match()` in `adjutant.py` only handles a small set of direct rules.
- It does not robustly split composite instructions.
- It does not cover shorthand commands well.
- Anything that falls through is handed to TaskAgent LLM planning, which is much less stable.

Concrete examples:

- `建造电厂，兵营，步兵`
  - current runtime will usually match only one building/job
  - it does not fan out into a deterministic multi-step plan
- `步兵3`
  - current runtime rule matcher does not reliably treat this as a production shorthand
  - it falls through to LLM planning

## 4. What the old NLU already has

`agents/nlu_gateway.py` + `nlu_pipeline/` already provide:

- intent model (`PortableIntentModel`)
- rule router (`CommandRouter`)
- confidence thresholds
- route-intent checks
- rollout gates
- risk levels
- execution success reporting
- composite command support (`composite_sequence`)

Evidence in repo:

- `nlu_pipeline/configs/runtime_gateway.yaml`
- `nlu_pipeline/rules/command_router.py`
- `nlu_pipeline/reports/live_game_e2e_report.*`
- `nlu_pipeline/reports/phase6_runtest_report*`

The old gateway also knows many command aliases that the current runtime misses, including:

- `展开基地车`
- `展开基地`
- `下基地`
- `步兵`/`火箭兵` style production forms
- composite clauses joined by `然后/再/并且/，`

## 5. Can the old NLU be integrated?

Yes, but **not** by restoring the old execution backend wholesale.

### 5.1 What can be reused safely

The reusable half is:

- intent classification
- command routing
- confidence / risk / rollout metadata
- composite intent detection
- entity extraction

This part can sit **before** Adjutant LLM routing.

### 5.2 What should not be reused directly

The old gateway currently renders Python code templates and executes them through:

- `the_seed.core.SimpleExecutor`

Those templates call raw API-like actions, for example:

- `api.deploy_mcv_and_wait(...)`

This does not match the current architecture goal, where:

- Adjutant / TaskAgent choose intent
- Kernel creates task/job
- Experts execute

So the old execution backend should **not** be reintroduced as-is.

## 6. Recommended integration shape

Best integration plan:

1. Reuse `Phase2NLUGateway` front half:
   - intent + confidence + route metadata
2. Stop before old `SimpleExecutor` code execution
3. Convert routed intents into current runtime actions:
   - direct rule-routed job start for simple safe intents
   - deterministic multi-step expansion for safe composite sequences
   - fallback to TaskAgent only when:
     - low confidence
     - unsafe intent
     - unsupported composite shape
4. Surface NLU metadata to debug UI:
   - `intent`
   - `confidence`
   - `route_intent`
   - `risk_level`
   - `execution_path`

## 7. Main problems found

1. Launch path defaults to debug logging, causing unnecessary OpenCodeAlert console spam.
2. Live runtime NLU path and old tested NLU path are currently split.
3. Current Adjutant rule matcher is too thin for composite and shorthand commands.
4. Falling through to TaskAgent LLM causes bizarre plans for commands that old NLU likely handled deterministically.
5. Old NLU is reusable, but only its routing/classification layer should be brought back; not its old code-execution path.

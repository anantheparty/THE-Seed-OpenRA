# xi Phase 0 Audit

Audited scope:
- `models/enums.py`
- `models/configs.py`
- `models/core.py`
- `models/__init__.py`
- `llm/provider.py`
- package skeleton `__init__.py` files

Reference spec:
- `docs/wang/design.md` sections `Task`, `Job`, `Expert Config Schema`, `ResourceNeed`, `Constraint`, `ExpertSignal`, `Event`, `NormalizedActor`, `TaskMessage`, `PlayerResponse`, and `Task Agent Tools`

## Findings

### 1. `Job.config` is not actually schema-bound to `expert_type`
- Severity: blocker
- Files:
  - `models/core.py:47-54`
  - `models/configs.py:47-54`
  - `docs/wang/design.md:76-77`
  - `docs/wang/design.md:200`

`design.md` says `Job.config` is a strong-typed `ExpertConfig` whose schema is defined by Expert type, and also says the framework validates config schema when Task Agent starts a job. The current model layer does not encode that relationship.

Right now:
- `expert_type` is just `str`
- `config` is a plain union of all config dataclasses
- there is no discriminant, registry, or validator tying `CombatExpert -> CombatJobConfig`, `ReconExpert -> ReconJobConfig`, etc.

That means a logically invalid pair such as `expert_type="CombatExpert"` with `ReconJobConfig(...)` is still representable by the model layer. As written, the schema is descriptive, not enforceable.

Recommended fix:
- add an explicit `ExpertType` enum or literal set
- add a validation helper / registry mapping expert type to config class
- make `start_job` validation use that mapping

### 2. `AnthropicProvider` is not actually provider-compatible for multi-turn tool use
- Severity: blocker
- Files:
  - `llm/provider.py:182-223`
  - `docs/wang/design.md:199-221`

The abstraction claims all LLM usage goes through one provider surface, and the Task Agent design depends on multi-turn tool use. `AnthropicProvider.chat()` converts tool definitions, but it does not convert OpenAI-format message history into Anthropic message blocks for tool-use conversations.

Current behavior:
- system message is extracted
- every non-system message is passed through unchanged
- there is no handling for OpenAI-style `tool` messages
- there is no conversion of assistant tool calls / tool results into Anthropic `tool_use` / `tool_result` content blocks

So the interface is only safe for plain chat, not the exact tool-using Task Agent loop the design requires. In practice this means the abstraction is not yet genuinely swappable across providers.

Recommended fix:
- define one canonical internal transcript format for tool turns
- add explicit OpenAI <-> Anthropic message conversion
- include at least one test covering assistant tool call + tool result + follow-up turn

### 3. Config fields are still too stringly typed for a “strong-format” schema
- Severity: should fix
- Files:
  - `models/configs.py:17-44`
  - `models/enums.py:68-79`

The design positions these configs as the schema boundary for job creation. But fields that already have closed vocabularies are still plain `str`, for example:
- `CombatJobConfig.engagement_mode`
- `MovementJobConfig.move_mode`

The project already defines `EngagementMode` and `MoveMode` enums, so not using them weakens the schema and makes invalid config values trivially representable.

Recommended fix:
- use enums in config dataclasses where the vocabulary is closed
- keep serialization to string at the boundary layer, not in the core schema layer

## Confirmed Good

### Drift fixes are in place
- `TaskKind` is reduced to exactly two values: `instant` / `managed`
- `AutonomyMode` is gone
- This matches Wang’s requested drift correction

### Core model coverage is otherwise close to spec
- `Task`, `Job`, `ResourceNeed`, `Constraint`, `ExpertSignal`, `Event`, `NormalizedActor`, `TaskMessage`, and `PlayerResponse` all exist with the expected main fields
- timestamp propagation is already better than the table-level minimum in `design.md`

### `QwenProvider` choice is clear and coherent
- `QwenProvider` uses the OpenAI Python SDK (`AsyncOpenAI`) against DashScope’s OpenAI-compatible endpoint
- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- model default: `qwen-plus`

### `MockProvider` is testable
- It records `call_log`
- It returns deterministic queued responses
- I verified locally that an async `chat()` call returns the injected `LLMResponse` and logs the input messages

### Directory skeleton is fine
- The new package directories and `__init__.py` files exist and are structurally harmless

## Verdict

Not zero-gap yet.

The `TaskKind` / `AutonomyMode` drift issue is fixed, and the overall direction is good. But there are still 2 real implementation blockers:
- config schema is not mechanically bound to expert type
- Anthropic provider does not correctly support the multi-turn tool-use transcript model

Everything else I found is secondary to those two.

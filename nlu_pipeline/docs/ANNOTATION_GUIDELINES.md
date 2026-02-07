# Annotation Guidelines

## Intent Definitions
- `deploy_mcv`: deploy/spread MCV or conyard expansion actions.
- `produce`: produce/train/build units or structures.
- `attack`: explicit attack/engage commands.
- `explore`: scout/recon/map-vision commands.
- `mine`: harvesting/economy mining commands.
- `query_actor`: ask for unit counts/status/lookup.
- `composite_sequence`: multi-step command joined by connectors (e.g. then/after).
- `fallback_other`: non-command/chat/system/meta/ambiguous requests.

## Slot Rules
- `count`: explicit number only; do not infer from context.
- `unit`: normalize to canonical OpenRA Chinese unit/building names where possible.
- `faction`: one of `己方/敌方/中立`.
- `range`: one of `all/screen/selected`.
- `actor_id`, `group_id`: integer ids parsed from text.

## Safety Policy
- Any sentence with negation or non-game context should default to `fallback_other` unless intent is explicit.
- Attack-like single characters in non-command context (e.g., “打开设置” contains “打”) must not be labeled as attack.

## Quality Process
- 2-pass review for high-risk intents (`attack`).
- Report ambiguous samples in review notes with reasons.

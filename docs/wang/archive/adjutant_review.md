# Audit of Adjutant interaction layer in `design.md` §6

## Verdict

The Adjutant direction is architecturally sound: moving all player interaction behind a single surface solves the earlier ambiguity around input routing and multi-task dialogue.

But the new interaction layer is **not yet fully implementation-ready**. I found **4 concrete gaps**, including **1 direct role contradiction**.

## Findings

### 1. Adjutant's query role directly contradicts its stated non-responsibilities

The design says:

- all player queries go through Adjutant and are answered by `Adjutant 直接 LLM+WorldModel 回答，不进 Kernel` ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L27))
- examples include `战况如何？`, `从哪进攻？`, `现在该做什么？` as direct query answers ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L574))

But the Adjutant section also says Adjutant:

- `不理解游戏细节`
- `不做战术决策`

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L303))

That is a direct contradiction. Queries like `从哪进攻？` and `现在该做什么？` inherently require game understanding and strategic judgment.

Implementation consequence:

- either Adjutant is only a router/formatter, in which case query answering belongs elsewhere
- or Adjutant is allowed to do bounded game-state interpretation for query-only answers, in which case the “不理解游戏细节 / 不做战术决策” lines must be narrowed

### 2. The structured Task-to-player / player-to-Task API is referenced, but not formally specified anywhere

Section 6 says Task Agents communicate with players through structured message types:

- `task_info`
- `task_warning`
- `task_question`
- `task_complete_report`

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L308))

And the routing example says Adjutant sends back:

- `player_response(question_id, answer="继续")`

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L357))

But none of that exists in the formal contracts:

- no Task Agent tool for emitting these messages
- no message schema with required fields
- no inbound schema for `player_response`
- no declared `question_id` lifecycle

The only formal runtime message schemas currently defined are:

- `ExpertSignal` (Job → Task Agent) ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L143))
- `Event` (WorldModel → Kernel) ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L155))
- Task Agent tools for Job control / constraints / queries ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L204))

So the new interaction layer still depends on an undeclared contract surface.

### 3. Pending-question timeout ownership is not specified end-to-end

The Adjutant context stores:

- `asked_at`
- `timeout_s`

inside `pending_questions` ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L333))

The routing examples also say:

- `pending_question 超时 → TaskA 用 default_if_timeout`

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L369))

And elsewhere the runtime already uses `default_if_timeout` inside `ExpertSignal.decision` for Job-side decision requests ([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L143)).

What is still missing:

- who owns the timeout clock: Adjutant or Task Agent
- how timeout is delivered back to the waiting Task Agent
- what happens if a late player reply arrives after default execution
- whether Task Agent questions always need a `default_if_timeout`, or only some do

This blocks the second requested routing scenario as written:

- `TaskA` asks a question
- player issues a new command instead
- the old question times out

The outcome is described, but the runtime path is not.

### 4. Simultaneous-question routing is still under-specified

The design claims this flow:

1. TaskA asks one question
2. TaskB asks another question
3. Adjutant presents both
4. player replies once: `放弃进攻，改目标`
5. Adjutant parses that into two separate replies

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L378))

This is plausible, but the routing contract still lacks the rules needed to make it deterministic:

- how each pending question is identified beyond `task_id`
- whether one utterance may satisfy multiple pending questions by default
- what happens if the reply is partial or ambiguous
- whether there is a one-question-at-a-time fallback policy
- whether priority order affects parse authority or only presentation order

Without those rules, the third requested scenario is still only narrative, not implementable behavior.

## Cross-check of the three requested routing scenarios

### 1. Response routing

Intended flow is coherent:

- Task asks question
- Adjutant records pending question
- player says `继续`
- Adjutant routes response back

But it is still blocked by the missing formal message contract:

- no declared `task_question` schema
- no declared `player_response` schema
- no defined `question_id` generation and matching

### 2. New command during pending question

Intent is coherent:

- player command should not be misclassified as an answer
- new Task is created
- old question later falls back to default

But it is still blocked by timeout lifecycle ambiguity:

- no owner for timeout execution
- no late-reply policy

### 3. Simultaneous questions

Conceptually valuable, but currently the weakest of the three.

The document gives an example outcome, but does not define enough rules to implement it safely and deterministically.

## Secondary integration note

Section 6 describes Adjutant as a rich interaction layer for both text mode and dashboard mode, including:

- Task question presentation
- button-based answers
- task completion reports

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L319))

But the formal WebSocket outbound list still only names:

- `world_snapshot`
- `task_update`
- `task_list`
- `log_entry`
- `player_notification`
- `query_response`

([design.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/wang/design.md#L409))

That may be salvageable if Wang intends `task_update` and `player_notification` to carry the Adjutant-layer payloads, but the mapping is not explicit yet.

## Bottom line

The Adjutant layer is the right direction, but I would not clear it as zero blockers yet.

The shortest path to closure is:

1. resolve the role contradiction:
   - either let Adjutant perform bounded query-only game interpretation
   - or move query answering behind another component and keep Adjutant as pure router/formatter
2. formally define the structured dialogue contract:
   - `task_info`
   - `task_warning`
   - `task_question`
   - `task_complete_report`
   - `player_response`
   - required IDs / fields / timeout metadata
3. define timeout ownership and late-reply behavior
4. define deterministic routing rules for multiple simultaneous pending questions

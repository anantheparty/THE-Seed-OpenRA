# Re-audit of Adjutant interaction layer in `design.md` §6

## Verdict

I clear the Adjutant layer as **zero blockers** for the 4 issues from the previous round.

All four previously reported gaps are now closed in substance:

1. query-role contradiction: closed
2. structured dialogue contract: closed
3. pending-question timeout ownership: closed
4. simultaneous-question routing rules: closed

## What changed

### 1. Query-role contradiction is closed

Previously, the design said Adjutant both:

- directly answered gameplay queries
- and did not understand game details / make tactical decisions

That contradiction is now resolved.

The revised text explicitly says:

- Adjutant recognizes a query
- then triggers an **independent LLM call** with WorldModel context
- Adjutant itself only triggers and forwards the result

That is enough to preserve the architectural boundary.

### 2. Structured Task/player dialogue contract is now explicit

Previously, `task_info`, `task_warning`, `task_question`, `task_complete_report`, and `player_response(...)` only existed narratively.

Now §6 formally defines:

- `TaskMessage` schema
  - `message_id`
  - `task_id`
  - `type`
  - `content`
  - `options`
  - `timeout_s`
  - `default_option`
  - `priority`
- `PlayerResponse` schema
  - `message_id`
  - `task_id`
  - `answer`

That closes the missing-contract gap in substance.

### 3. Pending-question timeout ownership is now explicit

Previously, the document said timeouts existed but did not define who owned them or how timeout fallback was delivered.

Now §6 explicitly states:

- Kernel owns the timer
- Kernel records `pending_question(message_id, task_id, timeout_s, default_option)`
- Kernel checks expiry every tick
- on expiry, Kernel sends `PlayerResponse(answer=default_option)` to the waiting Task Agent
- late player replies receive the notice:
  - `已按默认处理，如需更改请重新下令`

That is sufficient to make the timeout path implementable.

### 4. Simultaneous-question routing is now deterministic enough

Previously, the document gave only a narrative example of one player reply satisfying two pending questions.

Now §6 adds concrete deterministic rules:

- questions are displayed in priority order
- if the LLM can confidently split the reply, it routes to both Tasks
- if not, it only matches the highest-priority pending question
- unanswered questions stay pending until timeout, then use `default_option`

That is enough to make the multi-question scenario implementable without hidden policy guessing.

## Residual nits

I still see a couple of local editorial inconsistencies, but I do **not** treat them as blockers:

- the routing example says `player_response(question_id, answer="继续")`, while the formal schema uses `message_id`
- the sample `pending_questions` context object still omits some newly formalized fields like `message_id`, `default_option`, and `priority`

These are documentation cleanup items, not architecture or implementation blockers.

## Bottom line

For the specific 4 Adjutant gaps from the previous round, my conclusion is:

- **all 4 are now closed**
- **zero blockers remain**

If Wang wants the shortest actionable summary:

- the Adjutant layer is now consistent enough to proceed
- only a couple of example-text nits remain around field naming / sample completeness

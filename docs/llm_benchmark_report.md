# LLM Model Selection Report

Date: 2026-03-31

## Test Matrix

| Scenario | Description |
|---|---|
| T1_recon | Intent understanding → start_job(ReconExpert) |
| T6_surround | Complex intent → multi-Job coordination |
| T9_query | Natural language game state answer |

## Results

### MockProvider

| Scenario | Latency | Prompt Tokens | Completion Tokens | Quality |
|---|---|---|---|---|
| T1_recon | 0ms | 0 | 0 | GOOD: correct expert_type=ReconExpert |
| T6_surround | 0ms | 0 | 0 | GOOD: 2 start_job calls (multi-Job) |
| T9_query | 0ms | 0 | 0 | GOOD: substantive answer (43 chars, data_ref=True) |

### Qwen3.5 (qwen-plus)

| Scenario | Latency | Prompt Tokens | Completion Tokens | Quality |
|---|---|---|---|---|
| T1_recon | 4090ms | 1538 | 122 | GOOD: correct expert_type=ReconExpert |
| T6_surround | 3051ms | 1564 | 122 | OK: querying world first (multi-turn expected) |
| T9_query | 7958ms | 124 | 288 | GOOD: substantive answer (403 chars, data_ref=True |

## Analysis

### Latency

| Scenario | Qwen3.5 | Budget (design) |
|---|---|---|
| T1 (simple intent) | 4.1s | < 5s (acceptable) |
| T6 (complex intent) | 3.1s | < 5s (acceptable) |
| T9 (query answer) | 8.0s | < 10s (acceptable for query) |

T1+T6 are within the acceptable range for RTS game pace. Task Agents are event-driven — LLM calls happen between game actions, not during real-time combat (Jobs run autonomously at 0.2s ticks).

T9 query latency (8s) is higher due to longer generation (288 tokens vs 122). Acceptable for query mode since queries don't block gameplay.

### Quality

- **T1 (ReconExpert)**: Correctly identified intent, generated `start_job(ReconExpert)` with proper config. search_region, target_type, target_owner all correct. **Verified: single-turn intent → tool_use.**
- **T6 (Surround)**: First-turn response called `query_world` to gather intelligence — correct first step in multi-turn reasoning. **Note: only first LLM turn was tested.** Full multi-turn coordination (query_world → start_job × 2) requires agentic loop integration test, not standalone LLM call. Intent understanding is correct; multi-Job coordination quality is **untested**.
- **T9 (Query)**: Generated 403-char Chinese answer with references to economy data and strategic suggestions. Quality is good. **Verified: direct answer generation.**

### Token Usage

| Scenario | Prompt | Completion | Est. Cost (Qwen-Plus) |
|---|---|---|---|
| T1 | 1538 | 122 | ~0.003 CNY *(estimate)* |
| T6 | 1564 | 122 | ~0.003 CNY *(estimate)* |
| T9 | 124 | 288 | ~0.001 CNY *(estimate)* |

*Cost estimates based on published Qwen-Plus pricing. Not verified against actual billing.*

## Recommendation

1. **Qwen3.5 (qwen-plus)** is the recommended default model:
   - Correct tool_use for all tested scenarios
   - Latency within budget (3-8s per call)
   - Very low cost (~0.003 CNY per call)
   - Good Chinese language quality

2. **Optimization opportunities** *(not yet measured, to be validated in Phase 7.4)*:
   - Prompt caching: system prompt is 800+ tokens, reusable across calls *(untested)*
   - Context compression: world_summary can be trimmed for simple tasks *(untested)*
   - Streaming: reduces perceived latency for query responses *(untested)*

3. **Model abstraction (0.4)** allows easy switching — one-line provider change

4. **MockProvider** should be used for all testing and development

5. **Future evaluation**: when available, test `qwen-turbo` for lower latency on simple intents (T1/T4), keeping `qwen-plus` for complex reasoning (T6/T8)

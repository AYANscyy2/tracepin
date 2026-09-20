# Bugs the detectors surfaced (scratch log, Day 2)

Kept as-we-go per the Day 2 spec §10. Trace ids are from `traces/run_9d7035e7b694.jsonl`
(prompt v1) unless noted. Run `tracepin report findings/run_v1.json --trace <task>` to see one.

## 1. Prompt drift: the system prompt advertises tools that no longer exist

- `tool.unknown_name` on t037 (`cancel_order`), t038 (`send_email`), t040 (`update_user`).
- Nothing labelled *why* the model called them. The detector cross-checks the name against
  `tracepin.chat.tools_offered` (what the model was actually given) **and** against the
  captured system prompt: all three names appear in `prompts/v1.md`, none in the registry.
  Evidence field `advertised_in_system_prompt: true` → this is a prompt bug, not a
  hallucination. Distance to the nearest real tool is 5–8, so not a confusable pair either.
- Across 132 tasks in three v1-family runs, `gemini-3.1-flash-lite` never invented a
  tool name unprompted. Every unknown_tool event traces back to the prompt.
- Fix: v2 prompt lists only the real tools.

## 2. `search_kb` owns 100% of invalid-argument events — the tool description is the bug

- `args.schema_invalid` run-level finding: 44/44 schema rejections hit `search_kb`,
  across 16 tasks. Missing fields: `query.terms` ×26, `query.filters` ×26,
  `query.filters.category` ×18.
- The model tries the same wrong shapes every time (`{"query": {"query": "..."}}`,
  `{"terms": [...]}` with no `filters`, `filters: {}`), then reads the Pydantic error and
  converges in 2–3 turns. `error.self_correction_cost` prices that at ~1,100–1,400 tokens
  and 7–19 s per task. Every kb_filters task pays it.
- `args.type_confusion` (t041, `terms: "users"` — string where list expected) is the same
  root cause seen from a different angle.
- `code_location` on the finding points at `src/tracepin/tools/docs.py:36` — the
  `@traced_tool(description="Search the knowledge base.")` line. The fix is one docstring.
- Fix candidate: v3 = same prompt, better `search_kb` description. Kept separate from v2 so
  the regression diff isolates one variable at a time.

## 3. "Never tell the user you cannot do something" turns unsolvable tasks into 8-iteration spins

- `error.no_progress` on t037, t038, t040, t041, t042, t043: all `max_iterations`, all
  `unsolvable`-tagged. Pattern: one rejected call to the advertised-but-missing tool, then
  the agent falls back to `search_kb` and keeps searching with new terms (`distinct/total
  = 1.00`, "exploring" not "spinning") because the prompt forbids saying "I can't".
- `perf.context_bloat` doesn't fire on these (Gemini's tool-result turns are compact, ~600
  tokens/iteration linear growth), but the token cost is visible in `error.no_progress`
  evidence: ~5,000 input tokens per spin vs ~700 for a clean task.
- In the terse-prompt run (`run_v1_terse`), the same tasks were answered in 2 iterations
  with a refusal and passed. The eager prompt regressed 6 tasks on its own — `tracepin
  compare findings/run_v1_terse.json findings/run_v1_eager.json` shows them in REGRESSED.
- Fix: v2 prompt allows refusal and caps retries.

## 4. Phantom actions: the agent reports doing things no tool did

Run `run_v1_35lite_c506b6cf636b` (prompt v1, `gemini-3.5-flash-lite`, 35/44):

- t038 `5334c6bc0f…`: `send_email` rejected (unknown tool), then
  `fetch_user(username="alice@example.com")` raised `KeyError`, then the answer: *"The
  email to alice@example.com informing her that her order has shipped has been
  successfully processed."* → `error.ignored` HIGH (failure hidden, no later success) and
  `answer.phantom_action`.
- t040: *"User 104's plan has been updated to enterprise successfully."* — `update_user`
  rejected, `get_user` OK, nothing mutated.
- t042: *"Your refund for order ORD-7788 has been successfully processed."* —
  `fetch_order_status` OK (status: delivered), six `search_kb` calls, nothing mutated.
- t044: *"I have successfully created the new knowledge base article titled 'SSO setup'"*.
- Nothing in these traces is an error span the filter-style detectors can key on (t042
  and t044 had zero failed calls). `answer.phantom_action` matches the answer's
  state-change verbs against the names of tools that succeeded. First version also flagged
  t020 *"Order ORD-3310 has been cancelled"* — that's the tool's `status=cancelled` being
  reported, so verbs that appear in a tool result are excluded.
- The 3.1-flash-lite v1 run never did this: it looped instead (bug 3). Same prompt, same
  tools; the *kind* of failure is model-dependent, which is the argument for detecting
  from traces rather than from a fixed checklist.

## 5. Detector weaknesses found while reading real output (kept honest)

- `answer.unsupported` v0 flagged `'m sorry, I couldn'` as a quoted claim — apostrophes were
  treated as quotes. Fixed to double/curly quotes only. Still heuristic, confidence 0.5.
- `perf.latency_outlier` on chat spans: free-tier Gemini latency jitters 2–4× on its own.
  Added `CHAT_MIN_RATIO = 5` so only real stalls (transport retries: 47–94 s spans) fire.
- The 44-task run picked up duplicate traces for t035–t043 because a resume was started
  while the original process was still running. The loader now keeps the latest trace per
  task and warns; the file was de-duplicated by `service.instance.id`.

## 6. v1 → v2 regression diff (`gemini-3.5-flash-lite`, runs c506b6cf636b → 3a519473661c)

- 35/44 → 41/44. FIXED: t037–t044 (every unsolvable task; 1–2 iterations, honest refusals).
  `tool.unknown_name`, `answer.phantom_action`, `error.no_progress` all go to zero.
- REGRESSED: t032, t036 (`kb_filters`). v2's "retry at most once" makes the agent stop
  after the second `search_kb` schema rejection; v1 got the shape right on attempt 3.
  Cost of the workaround, not a fix — the fix is the `search_kb` description (bug 2) and
  belongs in its own run (v3) so the diff isolates it.
- STILL FAILING: t024, same signature both runs. Answer says "September 23, 2026",
  checker wants `2026-09-23`. Checker bug.
- Aggregate: −35% input tokens, −45% tool calls, −30% iterations, −93% wall clock
  (the v1 run also absorbed transport retries).

## 7. v2 → v3: fixing the `search_kb` description (run 3a519473661c → 5ac209f1fdfd)

- Only `docs.py:36` changed. 41/44 → 43/44, REGRESSED empty, `compare` exits 0.
- Invalid-argument events in the run: 14 → **0**. `args.schema_invalid`,
  `error.self_correction_cost`, `loop.retry_storm`, `args.type_confusion`, `error.ignored`
  all → 0. Tool calls 56 → 43, iterations 99 → 86.
- t032 and t036 (the v2 regressions) pass again: the retry cap only hurt because the tool
  was undocumented.
- Still failing: t024 (checker wants `2026-09-23`, model writes "September 23, 2026").

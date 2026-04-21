# Phase 1 Guardrail and Positioning Fix Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 修复三类交易输出问题：基本面/业绩预期对技术面的硬约束、buy 与仓位等级分离、sell/reduce 的统一风控动作。

**Architecture:** 保持 `AnalysisResult` 主结构稳定，不做大 schema 改造。将修复拆成三层：`src/analyzer.py` 负责生成约束收紧，`src/core/pipeline.py` 负责把基本面上下文喂完整并区分缺失类型，`src/analysis/normalization/` 负责硬规则与执行语义映射。`src/report_language.py`、`src/notification.py`、`src/services/history_service.py` 只补归因和展示，不承担业务决策。

**Tech Stack:** Python, pytest, existing normalization/reporting pipeline.

---

## Task 1: Lock down basic-fundamental guardrail inputs in the pipeline

**Objective:** 让 pipeline 明确区分“财报主链缺失”和“业绩预期缺失”，并把结构化输入稳定传给归一化层。

**Files:**
- Modify: `src/core/pipeline.py`
- Modify: `tests/test_analysis_api_contract.py`
- Modify: `tests/test_analysis_portfolio_context.py`（如需补上下文断言）

**Step 1: Write failing test**

Add a test asserting that when `fundamental_context` is present but earnings data is incomplete, the pipeline still emits a structured context that preserves the missing-type distinction instead of collapsing everything to generic fallback text.

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analysis_api_contract.py -k fundamental -v`
Expected: FAIL — missing explicit distinction or missing structured fields.

**Step 3: Write minimal implementation**

In `src/core/pipeline.py`, keep the existing fallback flow but normalize the gathered `fundamental_context` into explicit categories before analysis/normalization consumes it. Do not alter `AnalysisResult` schema.

Sketch:
```python
fundamental_quality = {
    "fundamental_data_unavailable": bool(...),
    "earnings_expectation_unavailable": bool(...),
}
```
Attach this to the runtime context or analysis payload used by normalization/reporting.

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analysis_api_contract.py -k fundamental -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/pipeline.py tests/test_analysis_api_contract.py tests/test_analysis_portfolio_context.py
git commit -m "feat: preserve fundamental quality signals in pipeline"
```

---

## Task 2: Add a hard risk-penalty veto rule without changing the result schema

**Objective:** 把 `risk_penalty` 从仓位影子标签升级成真正的硬否决输入：高风险时不允许 buy 继续保持 buy。

**Files:**
- Modify: `src/analysis/normalization/service.py`
- Modify: `tests/test_analysis_portfolio_context.py`
- Modify: `tests/test_phase2_position_strength.py`（改成真正验证规则，而不是只验证赋值）

**Step 1: Write failing test**

Add a test that builds an `AnalysisResult` with `decision_type='buy'` and `risk_penalty >= threshold`, then asserts normalization downgrades it to `hold`/`观望` (or the current canonical equivalent) and emits a `hard_guardrail` reason code.

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analysis_portfolio_context.py -k risk_penalty -v`
Expected: FAIL — no hard veto yet.

**Step 3: Write minimal implementation**

Extend `PositionStrengthRule` or add a new rule in `src/analysis/normalization/service.py` that:
- treats missing/NaN `risk_penalty` as `0.0`
- when `decision_type == 'buy'` and `risk_penalty >= threshold`, rewrites the decision to a conservative alternative
- records a guardrail reason code and field transition in `normalization_report`

Do not touch `AnalysisResult` fields.

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analysis_portfolio_context.py -k risk_penalty -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analysis/normalization/service.py tests/test_analysis_portfolio_context.py tests/test_phase2_position_strength.py
git commit -m "feat: add hard risk-penalty guardrail"
```

---

## Task 3: Separate direction from position sizing in normalization output

**Objective:** 让 buy 只表示方向，不再暗含仓位；仓位等级通过独立语义输出。

**Files:**
- Modify: `src/analysis/normalization/service.py`
- Modify: `tests/test_phase2_position_strength.py`
- Modify: `tests/test_agent_pipeline.py`
- Modify: `tests/test_notification.py`（如果要展示仓位语义）

**Step 1: Write failing test**

Add a test proving that a `buy` result with low risk maps to a more explicit position tier than plain `neutral`, and that the tier can be traced in normalization output.

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_phase2_position_strength.py -v`
Expected: FAIL for the new semantics.

**Step 3: Write minimal implementation**

Keep `decision_type` unchanged. Expand the position semantics in normalization output so `position_strength` becomes a real execution hint, not just a label. For the first pass, keep it inside `src/analysis/normalization/service.py` and derive it from both `decision_type` and `risk_penalty`. Surface it in `normalization_report` if needed for traceability.

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_phase2_position_strength.py tests/test_agent_pipeline.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analysis/normalization/service.py tests/test_phase2_position_strength.py tests/test_agent_pipeline.py
git commit -m "feat: separate direction from position semantics"
```

---

## Task 4: Add a统一 sell/reduce execution guardrail

**Objective:** 把清仓/减仓类输出收敛为一致的风控动作，避免只停留在结论层。

**Files:**
- Modify: `src/analysis/normalization/portfolio_rules.py`
- Modify: `src/report_language.py`
- Modify: `src/services/history_service.py`
- Modify: `src/notification.py`
- Modify: `tests/test_analysis_portfolio_context.py`
- Modify: `tests/test_analysis_history.py`

**Step 1: Write failing test**

Add a test covering a weak sell/reduce candidate and assert that the normalization result includes a unified action trace with the expected downgraded or standardized action.

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analysis_portfolio_context.py tests/test_analysis_history.py -k guardrail -v`
Expected: FAIL — no unified execution mapping yet.

**Step 3: Write minimal implementation**

In `portfolio_rules.py`, add a rule that standardizes sell/reduce outcomes into a small set of execution actions based on:
- technical weakness
- lack of positive catalysts
- existing position context
- risk severity

Make sure the rule writes `reason_code` and `field_transitions` so downstream rendering can explain it.

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analysis_portfolio_context.py tests/test_analysis_history.py -k guardrail -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analysis/normalization/portfolio_rules.py src/report_language.py src/services/history_service.py src/notification.py tests/test_analysis_portfolio_context.py tests/test_analysis_history.py
git commit -m "feat: unify sell and reduce guardrails"
```

---

## Task 5: Tighten analyzer prompts so basic fundamentals outrank weak technicals

**Objective:** 减少模型天生技术面偏重，避免生成层先把结果收得过保守。

**Files:**
- Modify: `src/analyzer.py`
- Modify: `tests/test_analyzer_news_prompt.py`
- Modify: `tests/test_report_schema.py`（如果需要验证生成约束）

**Step 1: Write failing test**

Add a test asserting the system prompt contains the rule that strong fundamental/earnings signals should prevent an automatic technical-only downgrade to hold/watch.

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analyzer_news_prompt.py -v`
Expected: FAIL — prompt lacks the new constraint.

**Step 3: Write minimal implementation**

Update prompt text in `src/analyzer.py` to specify:
- basic fundamentals / earnings outlook are hard constraints
- weak technical signals alone must not force conservative collapse when fundamentals are strong
- `buy` remains a direction signal, not a position-size signal

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analyzer_news_prompt.py tests/test_report_schema.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analyzer.py tests/test_analyzer_news_prompt.py tests/test_report_schema.py
git commit -m "feat: tighten analyzer fundamentals-first prompting"
```

---

## Final verification

After all tasks are done:

```bash
python -m pytest tests/test_analysis_portfolio_context.py tests/test_analysis_history.py tests/test_notification.py tests/test_report_renderer.py tests/test_analyzer_news_prompt.py tests/test_phase2_position_strength.py -q
```

Expected: all pass.

If you want to keep the first pass smaller, do Task 2 + Task 4 first. They are the most direct guardrail fixes.

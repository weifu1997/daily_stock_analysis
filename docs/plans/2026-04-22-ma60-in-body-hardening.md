# MA60 强制进正文实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 让最终报告正文里的均线分析稳定显式包含 MA60，而不是只靠模型自由发挥。

**Architecture:** 保持现有输出结构不动，只在 `src/analyzer.py` 内做两层加固：先收紧 prompt 让 LLM 必须写 MA60，再在结果落盘前做一个很轻的兜底补句。报告渲染层、通知层、历史层都不改，避免引入横向回归。

**Tech Stack:** Python、pytest、现有 `Analyzer` / report schema / notification 生成链路。

---

### Task 1: 给均线系统 prompt 加 MA60 硬约束

**Objective:** 让 `ma_analysis` 不能再忽略 MA60，模型输出阶段就把 MA60 放进正文。

**Files:**
- Modify: `src/analyzer.py`（`_get_analysis_system_prompt()` 中均线系统段，约在 1945-1953 行）
- Test: `tests/test_analyzer_ma60_prompt.py`（新建）

**Step 1: Write failing test**

```python
from src.analyzer import Analyzer


def test_system_prompt_requires_explicit_ma60_rule():
    analyzer = Analyzer.__new__(Analyzer)
    prompt = analyzer._get_analysis_system_prompt("zh", "603166")
    assert "MA60" in prompt
    assert "ma_analysis" in prompt
    assert "必须显式提到 MA60" in prompt
```

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analyzer_ma60_prompt.py -q`
Expected: FAIL before implementation, because the explicit hard-constraint sentence is not yet present.

**Step 3: Write minimal implementation**

在 `src/analyzer.py` 的均线系统段后追加约束，要求：
- `ma_analysis` 必须显式提到 MA60
- 必须说明当前价相对 MA60 的位置
- 必须说明 MA20 与 MA60 的关系
- 若 MA60 缺失，必须写出 `MA60=N/A`

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analyzer_ma60_prompt.py -q`
Expected: `1 passed`

---

### Task 2: 增加 MA60 文本兜底补句

**Objective:** 当 LLM 没写出 MA60 时，自动补一行中期趋势说明，保证正文里不会空掉。

**Files:**
- Modify: `src/analyzer.py`（在结果构造前加一个小辅助函数，并在 `ma_analysis=data.get('ma_analysis', '')` 处接入）
- Test: `tests/test_analyzer_ma60_fallback.py`（新建）

**Step 1: Write failing test**

```python
from src.analyzer import Analyzer


def test_ma60_fallback_appends_ma60_when_missing():
    analyzer = Analyzer.__new__(Analyzer)
    text = analyzer._ensure_ma60_in_ma_analysis(
        "均线走平，短线震荡。",
        {"ma60": 10.8, "ma20": 11.2, "close": 11.0},
        {"ma_status": "多头排列"},
    )
    assert "MA60" in text
    assert "中期" in text or "趋势" in text
```

**Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_analyzer_ma60_fallback.py -q`
Expected: fail until helper exists.

**Step 3: Write minimal implementation**

先补一个很小的辅助函数，建议放在 `src/analyzer.py` 的 prompt 方法后面、结果构造前：

```python
def _ensure_ma60_in_ma_analysis(self, ma_analysis: str, today: dict, context: dict) -> str:
    if not ma_analysis:
        ma_analysis = ""

    if any(k in ma_analysis for k in ("MA60", "60日", "中期趋势参考线")):
        return ma_analysis

    ma60 = today.get("ma60")
    close = today.get("close")
    ma20 = today.get("ma20")
    if ma60 is None or close is None:
        return ma_analysis

    pos_60 = "上方" if close > ma60 else "下方"
    if ma20 is not None:
        pos_20 = "上方" if close > ma20 else "下方"
        relation = f"MA20在{pos_20}，MA60在{pos_60}"
    else:
        relation = f"当前价在MA60{pos_60}"

    fallback = f"MA60：{relation}，中期趋势需重点关注。"
    return f"{ma_analysis}\n{fallback}".strip()
```

然后把结果构造中的 `ma_analysis=data.get('ma_analysis', '')` 改成：

```python
ma_analysis=self._ensure_ma60_in_ma_analysis(
    data.get('ma_analysis', ''),
    today,
    context,
),
```

**Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_analyzer_ma60_fallback.py -q`
Expected: `1 passed`

---

### Task 3: 做一次真实样本验证，确认正文里真的能看到 MA60

**Objective:** 验证最终输出链路没有把 MA60 再吞掉。

**Files:**
- Read-only verification against existing code path
- Optional: `tests/test_analyzer_ma60_integration.py`（如果需要补一条集成测试）

**Step 1: Run targeted tests**

Run:
```bash
python -m pytest tests/test_analyzer_ma60_prompt.py tests/test_analyzer_ma60_fallback.py -q
```
Expected: 两个测试都通过。

**Step 2: Run a targeted smoke on the分析入口**

找一条已经有 `ma60` 的样本，确认最终产物里的 `ma_analysis` 包含 MA60 关键词。优先看：
- `AnalysisResult.ma_analysis`
- `notification.py` 渲染出来的技术面段落
- `history_service.py` 生成的历史报告段落

**Step 3: 复核 report 渲染层不需要改**

如果正文已经带上 MA60，就不要改 `notification.py` / `history_service.py`，避免引入重复展示。

**Step 4: 如有必要再补一条集成测试**

仅当真实样本仍然看不到 MA60 时，再补一个集成测试，断言最终 `ma_analysis` 文本包含 `MA60`。

---

## 验收标准

- `src/analyzer.py` 的 prompt 明确要求 `ma_analysis` 写出 MA60
- `src/analyzer.py` 的后处理 helper 会在缺失时补一行 MA60
- 现有报告渲染层无需改动
- 新增测试覆盖 prompt 约束和兜底补句
- 真实报告正文里能看到 MA60，而不是只在底层数据里存在

## 不做的事

- 不改 `notification.py`
- 不改 `history_service.py`
- 不拆新 schema 字段
- 不重构整个分析模型输出结构

## 运行验证命令

```bash
python -m pytest tests/test_analyzer_ma60_prompt.py tests/test_analyzer_ma60_fallback.py -q
python -m py_compile src/analyzer.py tests/test_analyzer_ma60_prompt.py tests/test_analyzer_ma60_fallback.py
```

## 风险点

- prompt 变强后，LLM 可能输出更长的均线段；但这是可接受的，属于正文信息补强。
- 兜底句如果过于模板化，可能显得略机械；因此只补一行，不扩散到其他层。
- 如果 `today` 或 `context` 缺关键字段，helper 应该静默放过，不要把分析流程打断。

## 审查结论

- 方案范围收得对：只动 `src/analyzer.py`，不碰渲染层。
- 任务拆分正确：先 prompt 硬约束，再 helper 兜底，再真实样本验收。
- 需要注意一点：Task 1 的测试建议明确断言“硬约束句子”而不只是 `MA60` 出现，否则测试会太弱。
- 另一个小修正：Task 2 的 helper 入参里用了 `today` / `context`，实施时要确保在结果构造前能拿到这两个对象，或者在调用点传入实际上下文，而不是凭空引用。
- 其余部分没有明显问题，可以开始实施。

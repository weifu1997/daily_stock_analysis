# Issue 5: 模板与 dataclass 契约版本控制

## 背景
当前 prompt 模板（`src/analyzer.py`）和输出结构（`AnalysisResult` dataclass）之间存在隐式耦合：
1. prompt 中声明的字段与 `AnalysisResult` 字段没有显式映射
2. 模板文本变更后，没有版本号追踪
3. LLM 输出格式（json structure）与模板要求可能漂移
4. 报告模板（`templates/*.md`）与 `AnalysisResult` 字段没有一致性校验

## 目标
1. 建立 prompt template → LLM output schema → `AnalysisResult` dataclass 的显式契约
2. 引入模板版本号，变更时自动检测不兼容
3. 在测试层增加 prompt/schema 一致性校验
4. 报告渲染器在字段缺失时给出明确警告（而非静默跳过）

## 具体任务清单

### Phase 1: 契约映射文档
- [ ] 创建 `docs/contracts/prompt-to-result-mapping.md`
  - 列出 prompt 中每个 `{{variable}}` 对应的 `AnalysisResult` 字段
  - 标注哪些是必填、哪些是可选、哪些有 fallback
  - 标注 `technical_factor_summary` 的权重降级语义

### Phase 2: 模板版本号
- [ ] 在 `src/analyzer.py` 中增加 `PROMPT_VERSION = "1.0.0"`
- [ ] 在 `AnalysisResult` 中增加 `prompt_version: Optional[str]` 字段（记录生成时使用的模板版本）
- [ ] `GeminiAnalyzer.analyze()` 返回结果时填充 `prompt_version`
- [ ] 历史数据回填为 `NULL`（兼容）

### Phase 3: Schema 校验
- [ ] 新增 `tests/test_prompt_schema_contract.py`
  - 解析 `_build_data_input_prompt` 和 `_build_output_instruction_prompt` 中的占位符
  - 校验每个占位符都有对应的 `AnalysisResult` 字段或允许列表
  - 校验 `AnalysisResult` 中没有未在 prompt 中使用的字段（可选，允许提醒）
- [ ] 新增 `tests/test_report_template_completeness.py`
  - 加载所有 `.md` 模板
  - 校验模板中引用的字段在 `AnalysisResult` 中存在
  - 校验 `AnalysisResult` 中的必填字段至少被一个模板引用

### Phase 4: 运行时校验
- [ ] `normalize_analysis_result` 中增加 `prompt_version` 校验
  - 如果结果中的 `prompt_version` 与当前 `PROMPT_VERSION` 不一致，log warning
- [ ] `report_renderer.py` 中增加字段缺失警告
  - 渲染时如果某个模板变量在 `AnalysisResult` 中缺失，输出 `logger.warning` 而非静默跳过

### Phase 5: 变更检测
- [ ] CI 中增加契约校验步骤
  - 如果 `src/analyzer.py` 变更但 `PROMPT_VERSION` 未升级 → CI 失败
  - 如果 `AnalysisResult` 字段变更但映射文档未更新 → CI 失败

## 验收标准
- [ ] prompt/schema 映射文档与代码同步
- [ ] 所有分析结果记录 `prompt_version`
- [ ] 报告渲染时字段缺失有 warning
- [ ] CI 能检测模板-schema 漂移

## 预估工时
6-8 小时

## 优先级
**P3**（报告稳定性，当前靠人肉眼检查）

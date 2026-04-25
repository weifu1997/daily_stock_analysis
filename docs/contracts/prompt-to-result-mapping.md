# Prompt / Result Contract Mapping

This document is generated from current templates and should remain in sync with report templates.

## Template placeholders

### report_markdown.j2
- `adj_data` -> `(unmapped)`
- `buy_count` -> `(unmapped)`
- `checklist` -> `(unmapped)`
- `chip_data` -> `(unmapped)`
- `clean_sniper` -> `(unmapped)`
- `enriched` -> `(unmapped)`
- `get_result_guardrail_messages` -> `(unmapped)`
- `get_result_guardrail_traces` -> `(unmapped)`
- `guardrail_traces` -> `(unmapped)`
- `history_by_code` -> `(unmapped)`
- `hold_count` -> `(unmapped)`
- `institution_data` -> `(unmapped)`
- `labels` -> `(unmapped)`
- `localize_chip_health` -> `(unmapped)`
- `localize_operation_advice` -> `(unmapped)`
- `localize_trend_prediction` -> `(unmapped)`
- `portfolio_contexts` -> `(unmapped)`
- `position` -> `(unmapped)`
- `price_data` -> `(unmapped)`
- `report_date` -> `(unmapped)`
- `report_decision_map` -> `(unmapped)`
- `report_language` -> `AnalysisResult.report_language`
- `report_quality` -> `(unmapped)`
- `report_quality_map` -> `(unmapped)`
- `report_timestamp` -> `(unmapped)`
- `results` -> `(unmapped)`
- `sell_count` -> `(unmapped)`
- `summary_only` -> `(unmapped)`
- `trend_data` -> `(unmapped)`
- `vol_data` -> `(unmapped)`

### report_brief.j2
- `buy_count` -> `(unmapped)`
- `enriched` -> `(unmapped)`
- `hold_count` -> `(unmapped)`
- `labels` -> `(unmapped)`
- `localize_operation_advice` -> `(unmapped)`
- `portfolio_contexts` -> `(unmapped)`
- `report_date` -> `(unmapped)`
- `report_decision_map` -> `(unmapped)`
- `report_language` -> `AnalysisResult.report_language`
- `report_quality_map` -> `(unmapped)`
- `report_timestamp` -> `(unmapped)`
- `results` -> `(unmapped)`
- `sell_count` -> `(unmapped)`

### report_wechat.j2
- `buy_count` -> `(unmapped)`
- `enriched` -> `(unmapped)`
- `failed_checks` -> `(unmapped)`
- `get_result_guardrail_messages` -> `(unmapped)`
- `get_result_guardrail_traces` -> `(unmapped)`
- `guardrail_traces` -> `(unmapped)`
- `hold_count` -> `(unmapped)`
- `labels` -> `(unmapped)`
- `localize_operation_advice` -> `(unmapped)`
- `localize_trend_prediction` -> `(unmapped)`
- `ns` -> `(unmapped)`
- `portfolio_contexts` -> `(unmapped)`
- `report_date` -> `(unmapped)`
- `report_decision_map` -> `(unmapped)`
- `report_language` -> `AnalysisResult.report_language`
- `report_quality_map` -> `(unmapped)`
- `report_timestamp` -> `(unmapped)`
- `results` -> `(unmapped)`
- `sell_count` -> `(unmapped)`
- `summary_only` -> `(unmapped)`

## AnalysisResult fields tracked
- `AnalysisResult.analysis_summary`
- `AnalysisResult.buy_reason`
- `AnalysisResult.change_pct`
- `AnalysisResult.company_highlights`
- `AnalysisResult.confidence_level`
- `AnalysisResult.current_price`
- `AnalysisResult.dashboard`
- `AnalysisResult.data_sources`
- `AnalysisResult.decision_type`
- `AnalysisResult.fundamental_analysis`
- `AnalysisResult.hot_topics`
- `AnalysisResult.key_points`
- `AnalysisResult.ma_analysis`
- `AnalysisResult.market_sentiment`
- `AnalysisResult.medium_term_outlook`
- `AnalysisResult.model_used`
- `AnalysisResult.news_summary`
- `AnalysisResult.operation_advice`
- `AnalysisResult.pattern_analysis`
- `AnalysisResult.prompt_version`
- `AnalysisResult.report_language`
- `AnalysisResult.risk_warning`
- `AnalysisResult.search_performed`
- `AnalysisResult.sector_position`
- `AnalysisResult.sentiment_score`
- `AnalysisResult.short_term_outlook`
- `AnalysisResult.stock_name`
- `AnalysisResult.technical_analysis`
- `AnalysisResult.trend_analysis`
- `AnalysisResult.trend_prediction`
- `AnalysisResult.volume_analysis`

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from src.analysis.candidate_layers import CandidateScoreResult, build_candidate_metrics, score_metrics


class CandidateScoringService:
    """Local deterministic L2 candidate scoring service.

    This service is intentionally side-effect free. It does not decide whether to
    buy/sell and does not mutate the candidate pool; it only produces a
    reproducible second-layer score for reporting and later orchestration.
    """

    def score_candidate(
        self,
        *,
        code: str,
        name: str = "",
        daily_df: Optional[pd.DataFrame],
        trend_result: Any = None,
        fundamental_context: Optional[Dict[str, Any]] = None,
        realtime_quote: Any = None,
        portfolio_context: Any = None,
    ) -> CandidateScoreResult:
        metrics = build_candidate_metrics(
            code=code,
            name=name,
            daily_df=daily_df,
            trend_result=trend_result,
            fundamental_context=fundamental_context,
            realtime_quote=realtime_quote,
        )
        return score_metrics(metrics)

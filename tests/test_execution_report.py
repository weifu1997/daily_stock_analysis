# -*- coding: utf-8 -*-

from src.runtime.execution_report import DegradedComponent, ExecutionReport


def test_execution_report_defaults_to_clean_success():
    report = ExecutionReport(success=True)

    assert report.success is True
    assert report.failed is False
    assert report.degraded is False
    assert report.fatal_error is None
    assert report.degraded_components == []
    assert report.warnings == []
    assert report.artifacts == {}


def test_execution_report_supports_degraded_success():
    report = ExecutionReport(success=True)
    report.add_degraded_component(
        name="search_service",
        status="failed",
        reason="initialization_error",
    )

    assert report.success is True
    assert report.failed is False
    assert report.degraded is True
    assert len(report.degraded_components) == 1

    item = report.degraded_components[0]
    assert item == DegradedComponent(
        name="search_service",
        status="failed",
        reason="initialization_error",
    )


def test_execution_report_supports_fatal_failure():
    report = ExecutionReport(
        success=False,
        fatal_error="pipeline crashed",
    )

    assert report.success is False
    assert report.failed is True
    assert report.fatal_error == "pipeline crashed"
    assert report.degraded is False


def test_execution_report_collects_warnings_and_artifacts():
    report = ExecutionReport(success=True)

    report.add_warning("search unavailable")
    report.add_warning("using stale fx rate")
    report.set_artifact("query_id", "abc123")
    report.set_artifact("stock_count", 5)

    assert report.warnings == [
        "search unavailable",
        "using stale fx rate",
    ]
    assert report.artifacts == {
        "query_id": "abc123",
        "stock_count": 5,
    }


def test_adding_degraded_component_marks_report_degraded():
    report = ExecutionReport(success=True, degraded=False)

    report.add_degraded_component(
        name="feishu_doc",
        status="skipped",
        reason="not_configured",
    )

    assert report.degraded is True
    assert report.degraded_components[0].status == "skipped"


def test_failure_report_can_also_be_degraded():
    report = ExecutionReport(
        success=False,
        degraded=True,
        fatal_error="main pipeline failed",
        degraded_components=[
            DegradedComponent(
                name="search_service",
                status="failed",
                reason="init_error",
            )
        ],
    )

    assert report.failed is True
    assert report.degraded is True
    assert report.fatal_error == "main pipeline failed"
    assert report.degraded_components[0].name == "search_service"

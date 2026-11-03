"""Streamlit research dashboard (thin client over the research API).

Run: `streamlit run src/equitydesk/dashboard/app.py` with `EQUITYDESK_API_URL` pointing at the API.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, time
from typing import Any

import streamlit as st

from equitydesk.dashboard import views
from equitydesk.dashboard.client import ApiClient, ApiError

CHART_METRICS = ("revenue_growth", "gross_margin", "operating_margin", "net_margin", "fcf_margin")


def client() -> ApiClient:
    if "api_client" not in st.session_state:
        st.session_state["api_client"] = ApiClient(
            os.environ.get("EQUITYDESK_API_URL", "http://localhost:8000")
        )
    api: ApiClient = st.session_state["api_client"]
    return api


def show_error(exc: ApiError) -> None:
    st.error(f"{exc.message} ({exc.code})")
    candidates = exc.details.get("candidates") if isinstance(exc.details, dict) else None
    if candidates:
        st.table(candidates)


def sidebar() -> None:
    st.sidebar.header("Company search")
    query = st.sidebar.text_input("Ticker, CIK, ISIN or name", key="query")
    exchange = st.sidebar.text_input("Exchange (MIC, optional)", key="exchange")
    if st.sidebar.button("Resolve", key="resolve") and query:
        st.session_state["resolution"] = client().resolve(query, exchange or None)
    resolution = st.session_state.get("resolution")
    if resolution:
        if resolution["status"] == "resolved":
            st.sidebar.success(f"{resolution['company']['name']} ({resolution['security_id']})")
        else:
            st.sidebar.warning(f"{resolution['status']}: {resolution['message']}")
            if resolution["candidates"]:
                st.sidebar.table(resolution["candidates"])

    st.sidebar.header("Launch research run")
    as_of_day = st.sidebar.date_input(
        "As of (UTC end of day)", value=datetime.now(UTC).date(), key="as_of"
    )
    if st.sidebar.button("Create run", key="create") and query:
        as_of = datetime.combine(as_of_day, time(23, 59, 59), tzinfo=UTC)
        as_of = min(as_of, datetime.now(UTC))
        try:
            run = client().create_run(query, as_of.isoformat(), exchange or None)
            st.session_state["run_id"] = run["research_run_id"]
        except ApiError as exc:
            show_error(exc)

    st.sidebar.header("Open existing run")
    run_id = st.sidebar.text_input(
        "Run id", value=st.session_state.get("run_id", ""), key="run_input"
    )
    if st.sidebar.button("Open", key="open") and run_id:
        st.session_state["run_id"] = run_id.strip()


def _optional(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def run_page(run_id: str) -> None:
    api = client()
    run = api.run(run_id)
    fraction, label = views.run_progress(run)
    st.subheader(f"Run {run_id}")
    st.caption(f"Company {run['company_id']} · as of {run['as_of']} · created {run['created_at']}")
    st.progress(fraction, text=label)

    tabs = st.tabs(["Sources", "Financials", "Valuation", "Scenarios", "Report", "History"])
    with tabs[0]:
        docs = api.documents(run_id)
        st.write(f"{len(docs)} documents published at or before as_of")
        if docs:
            st.dataframe(views.document_rows(docs), width="stretch")
    with tabs[1]:
        fin = api.financials(run_id)
        series = views.metric_series(fin, CHART_METRICS)
        if series:
            st.line_chart(series)
        rows = views.metric_rows(fin)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.info("No financial facts available at as_of.")
    vals = api.valuations(run_id)
    with tabs[2]:
        rows = views.valuation_rows(vals)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.info("No valuation records yet.")
    with tabs[3]:
        rows = views.scenario_rows(vals)
        if rows:
            st.dataframe(rows, width="stretch")
            scenario_val = next(v for v in vals if v["method"] == "scenario")
            pick = st.selectbox(
                "Assumptions of", [r["scenario"] for r in rows], key="scenario_pick"
            )
            st.dataframe(views.scenario_assumptions(scenario_val, pick), width="stretch")
        else:
            st.info("No scenario analysis yet.")
    with tabs[4]:
        report = _optional(api.report, run_id)
        if report is None:
            st.info("No report has been composed for this run yet.")
        else:
            passed = report["review_passed"]
            if passed is True:
                st.success("Independent review passed")
            elif passed is False:
                st.error("Independent review found blocking issues")
            findings = views.review_summary(report["review"])
            if findings:
                st.dataframe(findings, width="stretch")
            st.markdown(api.report_markdown(run_id))
    with tabs[5]:
        history_tab(api, run)


def history_tab(api: ApiClient, run: dict[str, Any]) -> None:
    runs = api.company_runs(run["company_id"])
    st.dataframe(views.history_rows(runs), width="stretch")
    refresh_day = st.date_input(
        "Refresh as of (UTC end of day)", value=datetime.now(UTC).date(), key="refresh_as_of"
    )
    if st.button("Refresh run", key="refresh"):
        as_of = min(datetime.combine(refresh_day, time(23, 59, 59), tzinfo=UTC), datetime.now(UTC))
        try:
            new = api.refresh_run(run["research_run_id"], as_of.isoformat())
            st.session_state["run_id"] = new["research_run_id"]
            st.rerun()
        except ApiError as exc:
            show_error(exc)
    others = [r["research_run_id"] for r in runs if r["research_run_id"] != run["research_run_id"]]
    default = run.get("refreshed_from")
    if others:
        against = st.selectbox(
            "Compare with",
            others,
            index=others.index(default) if default in others else 0,
            key="diff_against",
        )
        diff = api.diff(run["research_run_id"], against)
        st.caption(f"{diff['old_as_of']} → {diff['new_as_of']}")
        for title, rows in views.diff_sections(diff).items():
            st.markdown(f"**{title.capitalize()}** ({len(rows)} changes)")
            if rows:
                st.dataframe(rows, width="stretch")


def main() -> None:
    st.set_page_config(page_title="EquityDesk", layout="wide")
    st.title("EquityDesk Research")
    try:
        sidebar()
        run_id = st.session_state.get("run_id")
        if run_id:
            run_page(run_id)
        else:
            st.info("Search for a company and create a run, or open an existing run.")
    except ApiError as exc:
        show_error(exc)


main()

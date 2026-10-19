"""API integration tests: the real application in demo mode (SQLite + mock MCP servers).
All market data here is DEMO synthetic data."""

import json

import pytest

from app.devtools import demo_data as dd

pytestmark = pytest.mark.usefixtures("broker_logged_in")
API = "/api/v1"


async def get(demo, path, **params):
    r = await demo.client.get(API + path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ platform
async def test_health_and_request_id(demo):
    assert (await demo.client.get("/health/live")).json() == {"status": "ok"}
    r = await demo.client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert r.headers["x-request-id"] and r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
async def test_common_health_paths_answer_liveness_instead_of_404(demo, path):
    r = await demo.client.get(path)
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_api_root_points_to_the_web_app_instead_of_404(demo):
    # Opening the API port in a browser used to show a bare 404 and look like a broken app.
    r = await demo.client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "api" and body["docs"] == "/docs" and "5173" in body["web_app"]


async def test_errors_share_one_schema_with_request_id(demo):
    r = await demo.client.get(API + "/instruments/NSE:NOSUCH")
    assert r.status_code == 404
    e = r.json()["error"]
    assert e["code"] == "instrument_not_found" and e["request_id"] == r.headers["x-request-id"] and "message" in e
    r2 = await demo.client.get(API + "/instruments", params={"page": 0})
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "validation_error"
    assert r2.json()["error"]["details"][0]["field"] == "page"


async def test_metrics_endpoint_exposes_prometheus_counters(demo):
    await demo.client.get(API + "/instruments")
    text = (await demo.client.get("/metrics")).text
    assert "api_requests_total" in text and "connector_up" in text and "job_runs_total" in text


async def test_pipeline_result_recorded_as_job(demo):
    d = demo.refresh["detail"]
    assert d["prices"]["failed"] == 0 and d["prices"]["conflicts"] == 2 and d["metrics"]["incomplete_scores"] == 0
    j = await get(demo, f"/jobs/{demo.refresh['job_id']}")
    assert j["status"] == "succeeded" and j["kind"] == "full_refresh" and j["finished_at"]
    assert (await demo.client.get(API + "/jobs/999999")).status_code == 404


# ------------------------------------------------------------ sources
async def test_sources_status_reports_connectors_capabilities_and_policy(demo):
    s = await get(demo, "/sources/status")
    by = {c["key"]: c for c in s["connectors"]}
    assert by["nse_bhavcopy"]["state"] == "ok" and by["kite_mcp"]["state"] == "ok"
    assert "get_stock_history" in by["nse_bhavcopy"]["tools"]
    assert "place_order" in by["kite_mcp"]["blocked_tools"] and "place_order" not in by["kite_mcp"]["tools"]
    assert by["llm"]["state"] == "not_configured" and by["web_search"]["state"] == "not_configured"
    assert by["kite_mcp"]["latency_ms"] is not None and by["kite_mcp"]["last_success_at"]
    caps = s["capabilities"]
    assert caps["kite_mcp"]["order_placement"]["supported"] is False
    assert caps["nse_mcp"]["fundamentals"]["supported"] is False and caps["nse_mcp"]["history"]["tool"] == "get_stock_history"
    assert s["precedence"]["default"][0] == "nse_mcp"
    assert s["is_demo"] is True and s["coverage"]["source_conflicts"] == 2
    assert set(s["coverage"]["bars_by_provider"]) == {"nse_mcp", "kite_mcp"}


async def test_broker_login_state_endpoint(demo, mock_state):
    r = await demo.client.post(API + "/sources/broker/login")
    assert r.status_code == 200
    k = r.json()["kite_mcp"]
    assert k["state"] == "login_required" and not k["login_url"]  # links are issued only on click (login-link)
    link = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert link["login_url"].startswith("http")
    mock_state.kite_logged_in = True
    assert (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]["state"] == "authorized"


# ------------------------------------------------------------ instruments
async def test_instrument_list_search_and_pagination(demo):
    d = await get(demo, "/instruments", q="demoit", page_size=3)
    assert d["total"] == 6 and len(d["items"]) == 3 and d["items"][0]["symbol"] == "DEMOIT01"
    assert all(i["is_demo"] for i in d["items"])
    idx = await get(demo, "/instruments", instrument_type="INDEX")
    assert idx["total"] == 11


async def test_instrument_detail_has_reconciled_quote_score_and_peers(demo):
    d = await get(demo, "/instruments/NSE:DEMOIT01")
    q = d["quote"]["fields"]["last_price"]
    assert q["provider"] == "nse_mcp" and q["status"] == "delayed" and q["as_of"] and q["currency"] == "INR"
    sc = d["snapshot"]["score"]
    assert sc["complete"] and 0 <= sc["overall"] <= 100 and set(sc["pillars"]) == {"fundamentals", "valuation", "momentum", "risk", "context"}
    assert all(p["weight"] == pytest.approx(0.2) for p in sc["pillars"].values())
    assert sc["overall"] == pytest.approx(sum(p["score"] for p in sc["pillars"].values()) / 5, abs=0.11)
    assert sc["stance"] in ("Favorable", "Mixed", "Unfavorable") and sc["risk"]["label"] in ("Low", "Moderate", "High", "Very High")
    assert sc["risk"]["criteria"] and sc["confidence_rule"] and sc["entry_setup"]["rules"]
    assert sc["entry_setup"]["entry_zone"] is None and "backtested" in sc["entry_setup"]["entry_zone_reason"]
    assert d["snapshot"]["calculation_version"]["score"] == "score-v1"
    assert len(d["peers"]) == 6 and d["themes"]


async def test_financial_sector_score_is_complete_with_inapplicable_ratios_excluded(demo):
    d = await get(demo, "/instruments/NSE:DEMOFIN01")
    f = d["snapshot"]["score"]["pillars"]["fundamentals"]
    assert d["snapshot"]["score"]["complete"] is True
    assert f["metrics_total"] < 8 and f["metrics_used"] == f["metrics_total"]
    assert {m["name"] for m in f["metrics"] if m["peer_scope"] == "not_applicable"} >= {"debt_equity", "ebitda_margin", "interest_coverage"}
    m = {x["name"]: x for x in (await get(demo, "/instruments/NSE:DEMOFIN01/metrics"))["metrics"]}
    assert m["debt_equity"]["value"] is None and m["debt_equity"]["status"] == "not_applicable"
    assert m["roe"]["value"] is not None


async def test_metrics_are_traceable_and_missing_values_are_never_zero(demo):
    d = await get(demo, "/instruments/NSE:DEMOIT01/metrics")
    m = {x["name"]: x for x in d["metrics"]}
    roe = m["roe"]
    assert roe["status"] == "ok" and "PAT" in roe["formula"] and set(roe["inputs"]) == {"pat", "avg_equity"}
    assert roe["value"] == pytest.approx(roe["inputs"]["pat"] / roe["inputs"]["avg_equity"] * 100)
    for name in ("return_5y", "pe_history_percentile"):
        assert m[name]["value"] is None and m[name]["status"] == "insufficient_history" and m[name]["reason"]
    assert d["coverage"]["available"] < d["coverage"]["total"] and d["calculation_version"]["technicals"] == "technicals-v1"
    assert "TTM" in m["pe"]["formula"] or "latest annual" in m["pe"]["formula"]


async def test_history_shows_provider_per_bar_conflicts_and_lineage(demo):
    d = await get(demo, f"/instruments/NSE:{dd.CONFLICT_SYMBOL}/history")
    assert d["coverage"]["by_provider"] == {"nse_mcp": d["coverage"]["bars"]}
    assert len(d["conflicts"]) == 2 and all(c["chosen"] == "nse_mcp" and c["diff_pct"] > c["tolerance_pct"] for c in d["conflicts"])
    assert {x["provider"] for x in d["lineage"]} >= {"nse_mcp", "kite_mcp"}
    assert d["freshness"] in ("fresh", "stale") and d["currency"] == "INR" and "split_adjusted" in d["adjustment"]
    gap = await get(demo, f"/instruments/NSE:{dd.GAP_SYMBOL}/history", limit=10)
    assert [b["provider"] for b in gap["bars"]][-dd.GAP_BARS:] == ["kite_mcp"] * dd.GAP_BARS


async def test_split_is_stored_and_prices_are_adjusted(demo):
    d = await get(demo, f"/instruments/NSE:{dd.SPLIT_SYMBOL}/history", limit=400)
    acts = d["corporate_actions"]
    assert len(acts) == 1 and acts[0]["kind"] == "split" and acts[0]["ratio"] == pytest.approx(2.0)
    closes = [b["close"] for b in d["bars"]]
    assert min(b / a - 1 for a, b in zip(closes, closes[1:], strict=False)) > -0.25
    assert d["conflicts"] == []


async def test_index_history_comes_from_broker_only(demo):
    d = await get(demo, f"/instruments/NSE:{dd.BENCHMARK}/history", limit=5)
    assert {b["provider"] for b in d["bars"]} == {"kite_mcp"}
    nse = next(x for x in d["lineage"] if x["provider"] == "nse_mcp" and x["dataset"] == "history")
    assert nse["status"] == "not_supported"


async def test_financials_are_labelled_demo_and_reported(demo):
    d = await get(demo, "/instruments/NSE:DEMOIT01/financials")
    assert d["is_demo"] is True and d["unit"] == "crore" and d["currency"] == "INR" and len(d["periods"]) == 5
    assert d["source"] == "demo_generator" and d["kind"] == "reported"
    q = await get(demo, "/instruments/NSE:DEMOIT01/financials", period_type="quarterly")
    assert q["periods"] == [] and q["data_state"] == "missing" and q["data_state_reason"]


async def test_statement_upload_is_validated(demo):
    bad = dd.fundamentals("DEMOIT02")
    bad["periods"][0]["revenue"] = "lots"
    r = await demo.client.put(API + "/instruments/NSE:DEMOIT02/financials", json=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_statements"
    wrong = dd.fundamentals("DEMOIT03")
    r2 = await demo.client.put(API + "/instruments/NSE:DEMOIT02/financials", json=wrong)
    assert r2.status_code == 422 and "does not match" in r2.json()["error"]["message"]


# ------------------------------------------------------------ screener
async def test_screener_filters_sorts_and_paginates(demo):
    f = json.dumps([{"metric": "roe", "op": "gte", "value": 10}, {"metric": "return_6m", "op": "gt", "value": 0}])
    d = await get(demo, "/screener", filters=f, sort="roe", order="desc", page_size=5, columns="roe,return_6m,pe")
    assert d["total"] >= 1 and len(d["items"]) <= 5
    roes = [i["values"]["roe"] for i in d["items"]]
    assert roes == sorted(roes, reverse=True) and all(r >= 10 for r in roes)
    assert all(i["values"]["return_6m"] > 0 for i in d["items"])
    assert [c["key"] for c in d["columns"]] == ["roe", "return_6m", "pe"] and d["columns"][0]["unit"] == "pct"
    assert d["as_of"] and d["calculation_version"] and d["exchange"] == "NSE" and d["currency"] == "INR"
    p2 = await get(demo, "/screener", page=2, page_size=10, sort="symbol", order="asc")
    assert p2["page"] == 2 and p2["total"] == 48 and len(p2["items"]) == 10


async def test_screener_marks_missing_values_with_reasons(demo):
    d = await get(demo, "/screener", columns="return_5y,close", page_size=3)
    it = d["items"][0]
    assert it["values"]["return_5y"] is None and it["missing"]["return_5y"]["status"] == "insufficient_history"
    f = json.dumps([{"metric": "return_5y", "op": "gt", "value": -1000}])
    none = await get(demo, "/screener", filters=f)
    assert none["total"] == 0 and none["excluded_for_missing_filter_metric"] == 48


@pytest.mark.parametrize("params,code", [
    ({"filters": '[{"metric": "roe; DROP TABLE instruments", "op": "gt", "value": 1}]'}, "invalid_filters"),
    ({"filters": '[{"metric": "roe", "op": "regex", "value": 1}]'}, "invalid_filters"),
    ({"filters": "not json"}, "invalid_filters"),
    ({"sort": "password"}, "invalid_screen"),
    ({"columns": "roe,__class__"}, "invalid_screen"),
])
async def test_screener_rejects_unlisted_fields_and_operators(demo, params, code):
    r = await demo.client.get(API + "/screener", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


async def test_screener_csv_export_carries_metadata(demo):
    r = await demo.client.get(API + "/screener/export.csv", params={"columns": "close,roe", "sort": "symbol", "order": "asc"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert "DEMO SYNTHETIC DATA" in lines[0] and "metrics as of" in lines[0] and "currency INR" in lines[0]
    assert "unavailable, not zero" in lines[1]
    assert lines[2].startswith("symbol,name,sector") and "ROE (pct)" in lines[2]
    assert len(lines) == 3 + 48 and lines[3].startswith("DEMOAUTO01,")


# ------------------------------------------------------------ themes
async def test_themes_list_builtin_categories_and_unavailable_flags(demo):
    d = await get(demo, "/themes")
    by = {t["slug"]: t for t in d["items"]}
    assert len([t for t in d["items"] if t["is_builtin"]]) >= 60 and len(d["categories"]) == 10
    assert by["positive-earnings-revisions"]["available"] is False and by["positive-earnings-revisions"]["latest_snapshot"] is None
    pm = by["price-momentum"]
    assert pm["available"] and pm["latest_snapshot"]["period"] and pm["last_refreshed"] and pm["leaders"]
    assert d["next_refresh"] and d["refresh_schedule"] == "30 6 1 * *"
    assert len((await get(demo, "/themes", category="Income"))["items"]) == 5


async def test_theme_detail_has_ranked_constituents_methodology_and_exclusions(demo):
    d = await get(demo, "/themes/low-volatility")
    snap = d["snapshot"]
    assert [c["rank"] for c in snap["constituents"]] == list(range(1, len(snap["constituents"]) + 1))
    vols = [c["values"]["volatility_252d"] for c in snap["constituents"]]
    assert vols == sorted(vols) and all(v < 25 for v in vols)
    assert d["theme"]["methodology"] and d["theme"]["interpreted_filters"] and snap["versions"]["score"] == "score-v1"
    assert snap["exclusions"] and all(e["reason"] for e in snap["exclusions"])
    assert snap["metrics_as_of"] and snap["constituents"][0]["price_at_snapshot"] and "prices_moved_since_snapshot" in snap
    un = await get(demo, "/themes/earnings-momentum")
    assert un["snapshot"] is None and un["data_state"] == "not_supported" and "analyst_estimates" in un["data_state_reason"]


async def test_custom_theme_interpret_confirm_create_refresh_and_history(demo):
    text = "NSE companies with improving 3-year revenue and ROCE, low debt, positive 6-month relative strength, and moderate volatility"
    it = (await demo.client.post(API + "/themes/interpret", json={"text": text, "name": "Quality momentum"})).json()
    assert it["requires_confirmation"] and len(it["ambiguities"]) >= 3 and len(it["interpreted_filters"]) >= 6
    r = await demo.client.post(API + "/themes", json={"spec": it["spec"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_required"
    r = await demo.client.post(API + "/themes", json={"spec": it["spec"], "confirmed": True, "description": "test"})
    assert r.status_code == 201
    tid = r.json()["theme"]["id"]
    assert r.json()["theme"]["category"] == "Custom" and r.json()["theme"]["version"] == 1

    async def refresh():
        j = (await demo.client.post(API + f"/themes/{tid}/refresh")).json()
        for _ in range(100):
            st = await get(demo, f"/jobs/{j['job_id']}")
            if st["status"] != "running":
                return st
            import asyncio
            await asyncio.sleep(0.05)
        raise AssertionError("job did not finish")

    first = await refresh()
    assert first["status"] == "succeeded" and first["detail"]["revision"] == 1
    snap1 = (await get(demo, f"/themes/{tid}"))["snapshot"]
    # loosen the theme, which creates version 2, then refresh again in the same month
    spec2 = {**it["spec"], "filters": it["spec"]["filters"][:1]}
    p = await demo.client.patch(API + f"/themes/{tid}", json={"spec": spec2})
    assert p.status_code == 409
    p = await demo.client.patch(API + f"/themes/{tid}", json={"spec": spec2, "confirmed": True})
    assert p.status_code == 200 and p.json()["theme"]["version"] == 2
    second = await refresh()
    assert second["detail"]["revision"] == 2
    hist = (await get(demo, f"/themes/{tid}/snapshots"))["items"]
    assert [(h["revision"], h["theme_version"]) for h in hist] == [(2, 2), (1, 1)]
    again = (await get(demo, f"/themes/{tid}", snapshot_id=snap1["id"]))["snapshot"]
    assert again["constituents"] == snap1["constituents"] and again["spec"] == snap1["spec"], "the earlier snapshot is unchanged"
    audit = (await get(demo, "/audit/events"))["items"]
    assert {"theme.create", "theme.update", "theme.refresh"} <= {a["action"] for a in audit}


async def test_builtin_theme_cannot_be_edited_and_bad_specs_are_rejected(demo):
    t = (await get(demo, "/themes/price-momentum"))["theme"]
    r = await demo.client.patch(API + "/themes/price-momentum", json={"spec": t["spec"] | {"limit": 3}, "confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "theme_immutable"
    bad = {"name": "x", "filters": [{"metric": "os.system('rm -rf /')", "op": "gt", "value": 1}]}
    r2 = await demo.client.post(API + "/themes", json={"spec": bad, "confirmed": True})
    assert r2.status_code == 422


# ------------------------------------------------------------ sector rotation
async def test_rotation_snapshot_has_trails_quadrants_method_and_timestamps(demo):
    d = await get(demo, "/sector-rotation")
    assert d["benchmark"] == dd.BENCHMARK and d["version"] == "rrg-v1" and d["data_as_of"] and d["computed_at"]
    assert d["params"] == {"ratio_window": 12, "momentum_window": 5, "trail": 10, "frequency": "W-FRI"}
    assert len(d["sectors"]) == 10 and "RS-Ratio" in d["method"] and "do not predict" in d["interpretation"]
    for s in d["sectors"].values():
        assert s["status"] == "ok" and len(s["trail"]) == 10 and s["quadrant"] in ("Leading", "Weakening", "Lagging", "Improving")
        assert s["series_kind"] == "index" and set(s["returns"]) == {"1M", "3M", "6M", "12M"}
    assert len({s["quadrant"] for s in d["sectors"].values()}) >= 2
    assert d["stale"] is False and d["refresh_schedule"] == "0 */12 * * *"


async def test_rotation_on_demand_parameters_and_history(demo):
    d = await get(demo, "/sector-rotation", trail=4, ratio_window=8)
    assert d["on_demand"] is True and d["snapshot_id"] is None and d["params"]["trail"] == 4
    assert all(len(s["trail"]) == 4 for s in d["sectors"].values())
    assert (await demo.client.get(API + "/sector-rotation", params={"trail": 500})).status_code == 422
    snaps = (await get(demo, "/sector-rotation/snapshots"))["items"]
    assert len(snaps) >= 1 and snaps[0]["quadrants"] and snaps[0]["coverage"]["sectors_plotted"] == 10
    sec = (await get(demo, "/sectors"))["items"]
    assert len(sec) == 10 and all(x["quadrant"] and x["instruments"] >= 3 for x in sec)


async def test_overview_summarises_market_sectors_themes_and_freshness(demo):
    d = await get(demo, "/overview")
    assert d["benchmark"]["symbol"] == dd.BENCHMARK and len(d["benchmark"]["sparkline"]) == 60
    assert d["universe"]["instruments"] == 48 and d["universe"]["complete_scores"] == 48
    assert len(d["sectors"]) == 10 and len(d["top_scores"]) == 5 and d["theme_leaders"]
    assert d["market_breadth"]["state"] in ("ok", "checking")  # a plain page load never waits on NSE
    fresh = await get(demo, "/overview", fresh="true")  # the refresh button waits for the live check
    assert fresh["market_breadth"]["state"] == "ok" and fresh["market_breadth"]["source_id"] == "get_market_breadth"
    again = await get(demo, "/overview")
    assert again["market_breadth"]["state"] == "ok" and again["market_breadth"]["cache"] == "hit"
    assert d["jobs"] and d["as_of"] and d["is_demo"] is True


# ------------------------------------------------------------ research and concalls
async def test_research_degrades_honestly_without_llm_or_search(demo):
    r = await demo.client.post(API + "/research/stock", json={"instrument": "NSE:DEMOIT01"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "deterministic" and d["llm"]["available"] is False and "LLM_ENABLED" in d["llm"]["reason"]
    assert d["web"]["available"] is False and "off in demo mode" in d["web"]["reason"]
    rep = d["report"]
    assert rep["news"] == [] and rep["catalysts"] == []
    assert rep["pros"] and all(p["evidence"] and all(e in d["evidence"] for e in p["evidence"]) for p in rep["pros"])
    assert "No language-model" in rep["summary"]["text"] and d["disclaimer"] and d["entry_setup"]["state"]
    assert d["is_demo"] is True and d["metrics_as_of"]
    latest = await get(demo, "/instruments/NSE:DEMOIT01/research")
    assert latest["report_id"] == d["report_id"]
    idx = await demo.client.post(API + "/research/stock", json={"instrument": f"NSE:{dd.BENCHMARK}"})
    assert idx.status_code == 422


async def test_concall_analyzer_scores_follow_through_from_two_quarters(demo):
    lst = await get(demo, "/instruments/NSE:DEMOIT01/concalls")
    assert [t["label"] for t in lst["transcripts"]] == ["Q2 FY2027", "Q1 FY2027"] and all(t["is_demo"] for t in lst["transcripts"])
    r = await demo.client.post(API + "/instruments/NSE:DEMOIT01/concalls/analyze", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "heuristic" and d["score"]["confidence"] == "Low" and d["limitations"]
    assert d["previous"]["label"] == "Q1 FY2027" and d["current"]["label"] == "Q2 FY2027"
    by = {a["commitment"][:40]: a for a in d["assessments"]}
    verdict = lambda frag: next(a["verdict"] for k, a in by.items() if frag in a["commitment"])  # noqa: E731
    assert verdict("Pune delivery centre") == "delivered"
    assert verdict("Atlas analytics platform") == "deferred"
    assert verdict("reduce gross debt") == "delivered"
    assert verdict("attrition below 15") == "not_delivered"
    assert verdict("acquisition of the cloud consulting") == "not_addressed"
    assert all(a["grounded"] for a in d["assessments"])
    sc = d["score"]
    weighted = sum(a["credit"] * a["weight"] for a in d["assessments"]) / sum(a["weight"] for a in d["assessments"]) * 100
    assert sc["value"] == pytest.approx(weighted, abs=0.06) and sc["formula"] and sc["label"] and sc["assessed"] == len(d["assessments"])
    ov = await get(demo, "/concalls")
    mine = next(i for i in ov["items"] if i["symbol"] == "DEMOIT01")  # other tests may analyse other companies first
    assert mine["score"] == sc["value"]


async def test_concall_needs_consecutive_quarters_and_valid_uploads(demo):
    r = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls/analyze", json={})
    assert r.status_code == 409 and "no con-call transcripts" in r.json()["error"]["message"]
    form = {"fiscal_year": "2027", "quarter": "2", "source": "test upload"}
    short = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls", data={**form, "text": "too short"})
    assert short.status_code == 422 and "too short" in short.json()["error"]["message"]
    body = "Management said we expect revenue growth of 12 percent by next quarter. " * 20
    ok = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls", data=form, files={"file": ("q2.txt", body.encode(), "text/plain")})
    assert ok.status_code == 201 and ok.json()["transcript"]["label"] == "Q2 FY2027"
    r2 = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls/analyze", json={})
    assert r2.status_code == 409 and "Q1 FY2027" in r2.json()["error"]["message"]
    bad = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls", data={**form, "text": body, "source_url": "http://127.0.0.1/x"})
    assert bad.status_code == 422
    # Q1 FY2027 is missing, but Q4 FY2026 exists: the analysis proceeds with it and says so (reported 19 Oct 2026, HAL).
    older = {"fiscal_year": "2026", "quarter": "4", "source": "test upload"}
    assert (await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls", data={**older, "text": body})).status_code == 201
    r3 = await demo.client.post(API + "/instruments/NSE:DEMOIT02/concalls/analyze", json={})
    assert r3.status_code == 200, r3.text
    out = r3.json()
    assert out["previous"]["label"] == "Q4 FY2026" and out["current"]["label"] == "Q2 FY2027"
    cmp = out["comparison"]
    assert cmp == {**cmp, "requested_previous": "Q1 FY2027", "consecutive": False, "gap_quarters": 2}
    assert "Q1 FY2027" in cmp["warning"] and "Q4 FY2026" in cmp["warning"] and cmp["warning"] in out["limitations"]
    assert out["score"]["confidence"] != "High"
    stored = await get(demo, "/instruments/NSE:DEMOIT02/concalls")
    assert stored["latest"]["comparison"]["warning"] == cmp["warning"]


# ------------------------------------------------------------ watchlists and alerts
async def test_watchlist_lifecycle(demo):
    w = (await demo.client.post(API + "/watchlists", json={"name": "Core"})).json()
    assert (await demo.client.post(API + "/watchlists", json={"name": "Core"})).status_code == 409
    wid = w["id"]
    a = await demo.client.post(API + f"/watchlists/{wid}/items", json={"instrument": "NSE:DEMOIT01", "note": "watch margins"})
    assert a.status_code == 201 and a.json()["items"][0]["close"] and a.json()["items"][0]["stance"]
    assert (await demo.client.post(API + f"/watchlists/{wid}/items", json={"instrument": "NSE:DEMOIT01"})).status_code == 409
    lst = await get(demo, "/watchlists")
    assert lst["items"][0]["items"][0]["symbol"] == "DEMOIT01" and "No orders" in lst["note"]
    item = lst["items"][0]["items"][0]["item_id"]
    assert (await demo.client.delete(API + f"/watchlists/{wid}/items/{item}")).json()["items"] == []
    assert (await demo.client.delete(API + f"/watchlists/{wid}")).status_code == 409
    assert (await demo.client.delete(API + f"/watchlists/{wid}", params={"confirm": True})).json() == {"deleted": wid}


async def test_alerts_are_idempotent_and_do_not_duplicate_notifications(demo):
    from app.services import alerts as svc

    inst = (await get(demo, "/instruments/NSE:DEMOIT01"))["instrument"]["id"]
    body = {"kind": "price_move", "instrument_id": inst, "params": {"window": "3M", "pct": 0.01}, "idempotency_key": "price-move-demoit01"}
    r1 = await demo.client.post(API + "/alerts", json=body)
    r2 = await demo.client.post(API + "/alerts", json=body)
    assert r1.status_code == 201 and r1.json()["created"] is True and r2.json()["created"] is False and r1.json()["id"] == r2.json()["id"]
    async with demo.container.sessions() as s:
        first = await svc.evaluate_all(demo.container, s)
        second = await svc.evaluate_all(demo.container, s)
    assert first["triggered"] == 1 and second["triggered"] == 0
    lst = await get(demo, "/alerts")
    assert len(lst["history"]) == 1 and "DEMOIT01 moved" in lst["history"][0]["message"] and "never place orders" in lst["delivery_note"]
    aid = r1.json()["id"]
    assert (await demo.client.patch(API + f"/alerts/{aid}", json={"status": "paused"})).json()["status"] == "paused"
    for bad in ({"kind": "place_order", "idempotency_key": "x" * 10}, {"kind": "indicator_cross", "instrument_id": inst, "idempotency_key": "y" * 10,
                                                                        "params": {"metric": "nope", "op": "gt", "value": 1}}):
        assert (await demo.client.post(API + "/alerts", json=bad)).status_code == 422
    assert (await demo.client.delete(API + f"/alerts/{aid}", params={"confirm": True})).status_code == 200

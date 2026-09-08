"""Validate this design delivery, not a strategy engine or investment performance.

Requires PyYAML. Run: python check_delivery.py
No network access, source-code imports, brokerage actions or notifications.
"""
from __future__ import annotations

import ast
import json
import math
import re
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from urllib.parse import unquote

import yaml

BASE = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def blocks(text: str):
    opened = False
    language = ""
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("```"):
            if opened:
                yield language, "\n".join(body)
                opened = False
                body = []
            else:
                language = line[3:].strip()
                opened = True
        elif opened:
            body.append(line)
    require(not opened, "Unclosed Markdown code fence")


def check() -> dict:
    docs = sorted(BASE.glob("*.md"))
    require(len(docs) >= 5, "Missing design documents")
    source_text = (BASE / "01_SOURCE_AUDIT.md").read_text(encoding="utf-8")
    refs = set(re.findall(r"^\| (R\d{2}) \|", source_text, re.M))
    require(refs == {f"R{i:02d}" for i in range(1, 35)}, "Source index mismatch")
    counts = {"markdown_files": len(docs), "source_references": len(refs),
              "json_blocks": 0, "python_blocks": 0, "yaml_files": 0}
    for path in docs:
        text = path.read_text(encoding="utf-8")
        unknown = set(re.findall(r"\bR\d{2}\b", text)) - refs
        require(not unknown, f"Unknown source in {path.name}: {unknown}")
        for language, body in blocks(text):
            if language == "json":
                json.loads(body)
                counts["json_blocks"] += 1
            elif language == "python":
                ast.parse(body)
                counts["python_blocks"] += 1
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            target = path.parent / unquote(link.split("#", 1)[0])
            require(target.exists(), f"Missing relative link: {path.name}: {link}")

    configs = {}
    for path in (BASE / "configs").glob("*.yaml"):
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        require(isinstance(content, dict), f"Invalid YAML root: {path}")
        configs[path.name] = content
        counts["yaml_files"] += 1
    require(counts["yaml_files"] == 2, "Expected two parameter/catalog files")
    cfg = configs["srpa_breakout_retest_v0.1.yaml"]
    catalog = configs["strategy_catalog.yaml"]
    require(cfg["strategy"]["implementation_status"] == "NOT_IMPLEMENTED", "Overclaimed implementation")
    require(cfg["strategy"]["evidence_level"] == "E1", "Overclaimed evidence")
    require(cfg["runtime"]["default_mode"] == "research", "Unsafe default mode")
    require(not cfg["runtime"]["broker_execution_enabled"], "Broker must be disabled")
    require(not cfg["execution"]["intrabar_stop_target_fills"], "EOD mismatch")
    require(cfg["execution"]["real_position_created_only_by_execution_import"], "Fill accounting mismatch")
    require(not cfg["exit"]["stop_can_widen"], "Stop-widening prohibited")
    require(cfg["prediction"]["mode"] == "rule_only", "Model readiness mismatch")
    require(not cfg["validation"]["skip_counts_as_pass"], "Skipped tests cannot pass")
    require(cfg["safety"]["missing_required_profiles"] == "fail_closed", "Required profiles must fail closed")
    for field in ("market_rules_profile_id", "fee_profile_id", "calendar_profile_id", "benchmark_instrument_id"):
        require(cfg["market"][field] is None, f"Invented live profile: {field}")
    setup = cfg["setup"]
    require(setup["retest"]["earliest_session_after_breakout"] >
            setup["follow_through"]["earliest_session_after_breakout"], "State timing conflict")
    require(setup["maximum_setup_sessions"] >=
            setup["retest"]["latest_session_after_breakout"] +
            setup["confirmation"]["maximum_sessions_after_retest"], "Setup expiry conflict")
    require(set(cfg["prediction"]["reaction_classes"]) == {"hold", "break", "timeout"}, "Missing timeout class")
    require(cfg["prediction"]["immature_labels"] == "censored", "Immature labels misclassified")
    portfolio = cfg["portfolio"]
    require(0 < portfolio["risk_fraction_per_trade"] < portfolio["maximum_planned_risk_fraction"] < 1,
            "Risk limits inconsistent")
    require(math.isclose(portfolio["maximum_unique_symbols"] * portfolio["maximum_single_symbol_weight"],
                         portfolio["maximum_gross_long_weight"]), "Illustrative concentration limits mismatch")
    items = catalog["strategies"]
    require(len(items) == 10 and len({s["id"] for s in items}) == 10, "Catalog count or duplicate ID")
    for item in items:
        require(not item["default_live_enabled"], "Catalog has enabled live strategy")
        require(item["implementation_status"] == "NOT_IMPLEMENTED", "Catalog claims implemented strategy")
        require(set(item["source_refs"]).issubset(refs), "Catalog has unknown source")
    tests = set(re.findall(r"^\| (T\d{2}) \|", (BASE / "04_CODEX_IMPLEMENTATION.md").read_text(), re.M))
    require(tests == {f"T{i:02d}" for i in range(1, 37)}, "Acceptance test index mismatch")

    # Arithmetic checks of the illustrative specification only.
    D = Decimal
    risk_share = (D("10.05") - D("9.40")) + D("0.25") + D("0.03")
    pre = int((D("250") / risk_share).to_integral_value(rounding=ROUND_FLOOR))
    qty = (pre // 100) * 100
    net_rr = (D("11.40") - D("10.05") - D("0.03")) / (D("10.05") - D("9.40") + D("0.03"))
    require(pre == 268 and qty == 200 and risk_share * qty == D("186.00"), "Sizing example incorrect")
    require(net_rr >= D(str(cfg["entry"]["minimum_net_reward_risk"])), "Example RR fails its gate")

    # A hypothetical complete event sample, not market observations.
    contact, hold, broken, timeout, total = 200, 90, 60, 50, 1000
    require(hold + broken + timeout == contact, "Synthetic outcomes incomplete")
    wrong_product = D(contact) / total * D(hold) / (hold + broken)
    correct_joint = D(hold) / total
    repaired_product = D(contact) / total * D(hold + broken) / contact * D(hold) / (hold + broken)
    require(wrong_product == D("0.12") and correct_joint == D("0.09"), "Conditional example incorrect")
    require(repaired_product == correct_joint, "Conditional probability repair incorrect")

    # The range envelope cannot be smaller than its average constituent width.
    highs, lows = [11.0, 12.0, 10.8], [10.0, 10.5, 9.9]
    envelope_ratio = (max(highs) - min(lows)) / (sum(h-l for h, l in zip(highs, lows))/len(highs))
    require(envelope_ratio >= 1.0, "Envelope inequality contradicted")

    return {
        "scope": "DELIVERY_SANITY_ONLY_NOT_STRATEGY_BACKTEST",
        "status": "PASS",
        "counts": {**counts, "catalog_entries": len(items), "planned_acceptance_tests": len(tests)},
        "specification_invariants": "PASS",
        "sizing_example": {"quantity": qty, "budgeted_risk": str(risk_share * qty), "net_rr": str(net_rr)},
        "synthetic_conditional_probability_example": {"unadjusted_product": str(wrong_product), "correct_joint": str(correct_joint)},
        "upstream_repository_tests_run": False,
        "strategy_engine_implemented": False,
        "real_market_backtest_run": False,
        "live_performance_verified": False,
    }


if __name__ == "__main__":
    report = check()
    (BASE / "delivery_checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

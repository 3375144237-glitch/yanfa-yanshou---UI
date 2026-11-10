from __future__ import annotations

from collections import Counter

from .common import ReviewError, digest, finite_number, instruction_gaps, validate_snapshot


GEOMETRY = ("x", "y", "width", "height")
STYLE = ("font_family", "font_size", "font_weight", "line_height", "letter_spacing", "color", "background_color",
         "border_radius", "border_width", "padding_top", "padding_right", "padding_bottom", "padding_left", "gap")
DEFAULT_TOLERANCES = {"position": 1, "size": 1, "style": 0.5}


def in_view(element, viewport):
    b = element["bbox"]
    return b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["width"] <= viewport["width"] + 0.1 and b["y"] + b["height"] <= viewport["height"] + 0.1


def match_elements(design, actual, mappings):
    by_id = {e["id"]: e for e in actual["elements"]}
    matches, used = {}, set()
    # Explicit bindings take precedence. Never consume one runtime node twice.
    for d in design["elements"]:
        rule = mappings.get(d["id"], {})
        if not any(rule.get(k) for k in ("actual_id", "selector", "accessibility_id", "resource_id")):
            continue
        candidates = [a for a in actual["elements"] if d["id"] in a.get("mapped_design_ids", [])]
        if rule.get("actual_id"):
            candidates = [by_id[rule["actual_id"]]] if rule["actual_id"] in by_id else []
        if len(candidates) == 1 and candidates[0]["id"] not in used:
            a = candidates[0]
            matches[d["id"]] = (a, bool(rule.get("confirmed")), "explicit")
            used.add(a["id"])
        else:
            matches[d["id"]] = (None, False, "ambiguous" if candidates else "not_found")
    for d in design["elements"]:
        if d["id"] in matches:
            continue
        candidates = []
        for field in ("key", "name", "text"):
            value = str(d.get(field) or "").strip()
            if not value:
                continue
            # Require uniqueness on BOTH sides, including already consumed nodes.
            if sum(str(e.get(field) or "").strip() == value for e in design["elements"]) != 1:
                continue
            found = [a for a in actual["elements"] if str(a.get(field) or "").strip() == value]
            if len(found) == 1 and found[0]["id"] not in used:
                candidates = found
                break
        if candidates:
            a = candidates[0]
            matches[d["id"]] = (a, False, "automatic")
            used.add(a["id"])
        else:
            matches[d["id"]] = (None, False, "unmatched")
    return matches


def compare(case, design, actual, previous=None):
    validate_snapshot(design)
    validate_snapshot(actual)
    if previous is not None and previous.get("schema_version") != 2:
        raise ReviewError("复验需要研发版 schema_version=2 的 review.json")
    mappings = case.get("mappings", {})
    tolerances = dict(DEFAULT_TOLERANCES, **case.get("tolerances", {}))
    for k, v in tolerances.items():
        finite_number(v, f"tolerances.{k}")
        if v < 0:
            raise ValueError("容差不能为负数")
    gaps = instruction_gaps(design)
    same_width = abs(design["viewport"]["width"] - actual["viewport"]["width"]) <= 0.1
    # Hash actual design content, not only a manually assigned version string.
    baseline = digest({k: design.get(k) for k in ("version", "viewport", "elements", "instructions")})
    context = {
        "case": case["id"], "state": case["state"], "platform": actual.get("platform"),
        "environment": actual.get("environment", {}), "viewport": actual["viewport"],
        "baseline": baseline, "tolerances": tolerances,
        "check_rules": {k: {p: v for p, v in r.items() if p in ("copy", "properties", "actual_id", "selector", "accessibility_id", "resource_id")} for k, r in mappings.items()},
    }
    checks, findings, suggestions = [], [], {}
    matches = match_elements(design, actual, mappings)
    scope = set(case.get("scope", []))
    for d in design["elements"]:
        rule = mappings.get(d["id"], {})
        a, confirmed, method = matches[d["id"]]
        if a:
            suggestion = {"actual_id": a["id"], "confirmed": confirmed}
            if a.get("selector"):
                suggestion = {"selector": a["selector"], "confirmed": confirmed}
            suggestions[d["id"]] = suggestion
        props = rule.get("properties", [*GEOMETRY, *d.get("style", {}).keys()])
        props = ["presence", *dict.fromkeys(p for p in props if p in GEOMETRY or p in STYLE or p == "text")]
        if rule.get("copy") == "fixed" and "text" not in props:
            props.append("text")
        for prop in props:
            check_id = "UI-" + digest([case["id"], d["id"], prop])[:12]
            expected = True if prop == "presence" else d["bbox"].get(prop) if prop in GEOMETRY else (d.get("text") if prop == "text" else d.get("style", {}).get(prop))
            measured = None if a is None else (a["bbox"].get(prop) if prop in GEOMETRY else (a.get("text") if prop == "text" else a.get("style", {}).get(prop)))
            result, reason, delta = "unknown", "", None
            if scope and d["id"] not in scope:
                reason = "本轮范围外"
            elif not in_view(d, actual["viewport"]):
                reason = "设计元素超出当前采集范围"
            elif prop == "presence" and method == "not_found" and rule.get("confirmed") and same_width and actual.get("mapping_queries", {}).get(d["id"]) == "absent":
                result, measured, confirmed = "fail", False, True
            elif a is None:
                reason = "未找到可靠对应元素" if method != "ambiguous" else "多个元素匹配，需确认"
            elif prop == "presence":
                result, measured = "pass", True
            elif not in_view(a, actual["viewport"]):
                reason = "运行元素被视口裁切，需补采集"
            elif prop in GEOMETRY and not same_width:
                reason = "设计与运行逻辑宽度不同，需提供对应断点设计或明确约束"
            elif prop == "text" and rule.get("copy") != "fixed":
                reason = "文案未声明为固定内容，保留样式检查"
            elif expected is None or measured is None:
                reason = "属性不可读取或没有设计依据"
            else:
                if isinstance(expected, (float, int)) and isinstance(measured, (float, int)):
                    delta = round(measured - expected, 4)
                    tolerance = tolerances["position" if prop in ("x", "y") else "size" if prop in ("width", "height") else "style"]
                    result = "pass" if abs(delta) <= tolerance else "fail"
                else:
                    result = "pass" if expected == measured else "fail"
            check = {"id": check_id, "design_id": d["id"], "element": d.get("name", d["id"]),
                     "property": prop, "expected": expected, "actual": measured, "delta": delta,
                     "result": result, "reason": reason, "mapping_confirmed": confirmed,
                     "basis_complete": not gaps, "match_method": method}
            checks.append(check)
            if result == "fail":
                status = "confirmed" if confirmed and not gaps else "candidate"
                findings.append(dict(check, status=status, priority="P1" if prop == "presence" else "P2", workflow_status="待处理",
                    design_bbox=d["bbox"], actual_bbox=a["bbox"] if a else d["bbox"], actual_id=a["id"] if a else "未找到（框选为预期位置）",
                    figma_url=d.get("figma_url", ""), source_hint=rule.get("source", ""),
                    group=d.get("parent_id", d["id"]),
                    recommendation=f"检查「{d.get('name', d['id'])}」的 {prop}；按设计值修正，并复验同一页面状态。"))
    # Retain unresolved history even when a whole round could not measure an item.
    # findings contains this round's observations only, so it cannot be the ledger.
    prior_records = {x["finding"]["id"]: x for x in (previous or {}).get("issue_history", [])}
    for finding in (previous or {}).get("findings", []):
        prior_records[finding["id"]] = {"finding": finding, "context": previous["context"], "result": "still_present"}
    prior_by_id = {key: record["finding"] for key, record in prior_records.items()}
    for finding in findings:
        old = prior_by_id.get(finding["id"], {})
        finding["workflow_status"] = old.get("workflow_status", "待处理")
        finding["note"] = old.get("note", "")
    environment_known = bool(actual.get("platform") and actual.get("environment")) and "unspecified" not in actual.get("environment", {}).values()
    comparable = bool(previous and previous.get("context") == context and environment_known)
    current_checks = {c["id"]: c for c in checks}
    retest, history = [], []
    for old in prior_by_id.values():
        c = current_checks.get(old["id"], {})
        result = "not_rechecked"
        origin_context = prior_records[old["id"]]["context"]
        if environment_known and origin_context == context and c.get("mapping_confirmed") and c.get("basis_complete"):
            result = {"pass": "resolved", "fail": "still_present"}.get(c.get("result"), "not_rechecked")
        retest.append({"id": old["id"], "element": old.get("element"), "property": old.get("property"), "result": result})
        history.append({"finding": old, "context": origin_context, "result": result})
    history_by_id = {r["finding"]["id"]: r for r in history}
    for finding in findings:
        history_by_id[finding["id"]] = {"finding": finding, "context": context, "result": "still_present" if finding["status"] == "confirmed" else "not_rechecked"}
    active_checks = [c for c in checks if c["reason"] != "本轮范围外"]
    counts = Counter(c["result"] for c in active_checks)
    verified = sum(c["result"] in ("pass", "fail") and c["mapping_confirmed"] and c["basis_complete"] for c in active_checks)
    warnings = []
    if gaps:
        warnings.append("设计说明未完整读取：" + ", ".join(gaps) + "；差异作为候选，需补充依据")
    if not same_width:
        warnings.append("逻辑宽度不一致：已暂停几何判定，未拉伸页面消除差异")
    if previous and not comparable:
        warnings.append("复验条件或设计基准变化：旧问题均保留为未复验")
    if not environment_known:
        warnings.append("运行环境信息不足：不能可靠自动关闭上一轮问题")
    return {"schema_version": 2, "case": case["id"], "state": case["state"], "context": context,
            "design": {"version": design.get("version"), "viewport": design["viewport"]},
            "actual": {"platform": actual.get("platform"), "viewport": actual["viewport"]},
            "coverage": {"elements": len(design["elements"]), "checks": len(active_checks), "out_of_scope": len(checks)-len(active_checks), "verified_checks": verified,
                         "pass": counts["pass"], "fail": counts["fail"], "unknown": counts["unknown"],
                         "complete": bool(active_checks) and verified == len(active_checks)},
            "warnings": warnings, "checks": checks, "findings": findings, "retest": retest,
            "issue_history": list(history_by_id.values()),
            "suggested_mappings": suggestions}

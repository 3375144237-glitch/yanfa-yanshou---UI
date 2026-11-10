from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class ReviewError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def finite_number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReviewError(f"{label} 必须是有限数值")
    if positive and value <= 0:
        raise ReviewError(f"{label} 必须大于 0")
    return value


def safe_name(value):
    return re.sub(r"[^\w.-]+", "-", str(value)).strip(".-")[:100] or "review"


def http_get(url, headers=None, binary=False):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ReviewError("接口地址必须使用 http 或 https")
    try:
        with urlopen(Request(url, headers=headers or {}), timeout=30) as response:
            data = response.read()
        return data if binary else json.loads(data)
    except HTTPError as error:
        # Do not include URLs, headers, or response bodies containing credentials.
        raise ReviewError(f"请求失败：HTTP {error.code}，请检查访问权限或服务状态") from None
    except (URLError, TimeoutError, OSError):
        raise ReviewError("请求失败：无法连接服务或读取超时") from None


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("elements"), list):
        raise ReviewError("快照需要 elements 数组")
    if snapshot.get("schema_version") != 1:
        raise ReviewError("snapshot.schema_version 必须为 1")
    viewport = snapshot.get("viewport", {})
    for field in ("width", "height"):
        finite_number(viewport.get(field), f"viewport.{field}", positive=True)
    if snapshot.get("coordinate_unit") != "logical_px":
        raise ReviewError("快照必须声明 coordinate_unit=logical_px，并先完成单位换算")
    seen = set()
    for element in snapshot.get("elements", []):
        if not isinstance(element, dict) or not isinstance(element.get("style", {}), dict):
            raise ReviewError("元素和 style 必须是对象")
        element_id = element.get("id")
        if not isinstance(element_id, str) or not element_id or element_id in seen:
            raise ReviewError("元素 ID 必须非空且唯一")
        seen.add(element_id)
        for field in ("x", "y", "width", "height"):
            finite_number(element.get("bbox", {}).get(field), f"{element_id}.bbox.{field}", positive=field in ("width", "height"))
        for key, value in element.get("style", {}).items():
            if isinstance(value, (int, float)):
                finite_number(value, f"{element_id}.style.{key}")
    return snapshot


def validate_case(case):
    if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or not isinstance(case.get("state"), str) or not case["state"]:
        raise ReviewError("配置需要非空字符串 id 和 state")
    if not isinstance(case.get("design"), dict) or not (case["design"].get("url") or case["design"].get("snapshot")):
        raise ReviewError("配置需要 design.url 或 design.snapshot")
    if not isinstance(case.get("actual"), dict) or case["actual"].get("type") not in ("web", "native", "snapshot"):
        raise ReviewError("actual.type 需要 web、native 或 snapshot")
    if not isinstance(case.get("mappings", {}), dict):
        raise ReviewError("mappings 必须为对象")
    supported = {"x", "y", "width", "height", "font_family", "font_size", "font_weight", "line_height", "letter_spacing", "color", "background_color", "border_radius", "border_width", "padding_top", "padding_right", "padding_bottom", "padding_left", "gap", "text"}
    for rule in case.get("mappings", {}).values():
        if not isinstance(rule, dict) or not isinstance(rule.get("confirmed", False), bool):
            raise ReviewError("映射规则必须为对象，confirmed 必须是布尔值")
        if rule.get("copy") not in (None, "fixed", "dynamic", "template", "unknown"):
            raise ReviewError("copy 需要 fixed/dynamic/template/unknown")
        if "properties" in rule and (not isinstance(rule["properties"], list) or any(p not in supported for p in rule["properties"])):
            raise ReviewError("properties 中含有不支持的属性")
    return case


def instruction_gaps(snapshot):
    instructions = snapshot.get("instructions", {})
    gaps = []
    for kind in ("comments", "annotations", "measurements"):
        entry = instructions.get(kind, {})
        if entry.get("status") not in ("read", "empty"):
            # An exception needs an explicit, recorded reason for this baseline.
            if not (entry.get("status") == "waived" and entry.get("reason")):
                gaps.append(kind)
    return gaps

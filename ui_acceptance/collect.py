from __future__ import annotations

import base64
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from .common import ReviewError, finite_number, http_get, read_json, validate_snapshot


def load_snapshot(config, base):
    path = (base / config["snapshot"]).resolve()
    snapshot = validate_snapshot(read_json(path))
    image = (base / config["image"]).resolve() if config.get("image") else None
    return snapshot, image


def parse_native_tree(xml, config, mappings):
    scale = finite_number(config.get("tree_units_to_logical"), "actual.tree_units_to_logical", positive=True)
    origin = config.get("tree_origin", {"x": 0, "y": 0})
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ReviewError("不支持包含 DTD 或实体声明的设备树")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ReviewError("Appium 返回了无法解析的页面 XML") from None
    elements = []

    def visit(node, path, ancestor_visible=True):
        attrs = node.attrib
        visible = ancestor_visible and attrs.get("visible", "true") != "false" and attrs.get("displayed", "true") != "false"
        if not visible:
            return
        box = None
        if attrs.get("bounds"):
            coords = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", attrs["bounds"])]
            if len(coords) == 4:
                x, y, right, bottom = coords
                box = {"x": x, "y": y, "width": right-x, "height": bottom-y}
        elif all(k in attrs for k in ("x", "y", "width", "height")):
            box = {k: float(attrs[k]) for k in ("x", "y", "width", "height")}
        if box and box["width"] > 0 and box["height"] > 0:
            box = {"x": (box["x"]-origin["x"])*scale, "y": (box["y"]-origin["y"])*scale,
                   "width": box["width"]*scale, "height": box["height"]*scale}
            access_id = attrs.get("content-desc") or attrs.get("name") or ""
            resource_id = attrs.get("resource-id", "")
            mapped = [k for k, rule in mappings.items()
                      if (rule.get("accessibility_id") and rule["accessibility_id"] == access_id)
                      or (rule.get("resource_id") and rule["resource_id"] == resource_id)]
            elements.append({"id": path, "key": access_id or resource_id, "name": access_id or resource_id,
                             "text": attrs.get("text") or attrs.get("label"), "type": attrs.get("type", node.tag),
                             "bbox": box, "style": {}, "mapped_design_ids": mapped})
        for i, child in enumerate(node):
            visit(child, f"{path}/{child.tag}[{i}]", visible)
    visit(root, root.tag)
    return elements


def collect_native(config, mappings, destination):
    server = config.get("server", "http://127.0.0.1:4723").rstrip("/")
    session = config.get("session_id")
    if not session:
        raise ReviewError("原生采集需要已有 Appium session_id；请先运行到要检查的页面")
    viewport = config.get("viewport", {})
    for field in ("width", "height"):
        finite_number(viewport.get(field), f"actual.viewport.{field}", positive=True)
    endpoint = f"{server}/session/{quote(session, safe='')}"
    def get(command):
        payload = http_get(endpoint + "/" + command)
        value = payload.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise ReviewError(f"Appium {command} 读取失败，请检查会话和当前 context")
        return value
    # Capture trees on both sides of the screenshot to detect obvious state drift.
    before = get("source")
    encoded = get("screenshot")
    after = get("source")
    if before != after:
        raise ReviewError("采集过程中页面元素树变化，请等待稳定或固定测试数据后重试")
    image_path = destination / "actual-source.png"
    try:
        image_path.write_bytes(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError):
        raise ReviewError("Appium 截图格式无效") from None
    snapshot = {"schema_version": 1, "coordinate_unit": "logical_px", "platform": config.get("platform", "native"),
                "viewport": viewport, "elements": parse_native_tree(before, config, mappings),
                "environment": {"device": config.get("device", "unspecified"), "runtime": config.get("runtime", "unspecified"),
                                "tree_units_to_logical": config["tree_units_to_logical"],
                                "tree_origin": config.get("tree_origin", {"x": 0, "y": 0})}}
    return validate_snapshot(snapshot), image_path


def collect_web(config, mappings, destination, base):
    task = {**config, "mappings": mappings, "output": str(destination.resolve())}
    if task.get("storage_state"):
        task["storage_state"] = str((base / task["storage_state"]).resolve())
    script = Path(__file__).resolve().parents[1] / "scripts/capture-web.mjs"
    # Configuration is passed via stdin, not placed in process arguments.
    try:
        process = subprocess.run([config.get("node", "node"), str(script)], input=json.dumps(task),
                                 text=True, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise ReviewError("未找到 Node.js；Web 采集需要 Node 20+ 和 npm install") from None
    except subprocess.TimeoutExpired:
        raise ReviewError("页面采集超时，请检查页面入口、登录状态和 ready_selector") from None
    if process.returncode:
        # Collector only emits a controlled error code; don't surface page/auth data.
        code = process.stderr.strip().splitlines()[-1] if process.stderr.strip() else "CAPTURE_FAILED"
        known = {"PLAYWRIGHT_MISSING", "BROWSER_MISSING", "CONFIG_INVALID", "PAGE_NOT_READY", "CAPTURE_FAILED"}
        raise ReviewError("Web 采集失败：" + (code if code in known else "CAPTURE_FAILED") + "，详见安装与接入文档")
    return validate_snapshot(read_json(destination / "actual.snapshot.json")), destination / "actual-source.png"

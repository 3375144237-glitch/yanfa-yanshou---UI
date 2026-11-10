from __future__ import annotations

import os
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .common import ReviewError, finite_number, http_get, read_json, validate_snapshot


def parse_figma_url(url):
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname not in ("figma.com", "www.figma.com") or len(parts) < 2 or parts[0] not in ("design", "file"):
        raise ReviewError("请提供 Figma design/file 节点链接")
    node = parse_qs(parsed.query).get("node-id", [""])[0].replace("-", ":")
    if not node:
        raise ReviewError("Figma 链接需要 node-id，用于确定检查画板")
    key = parts[1]
    if "branch" in parts:
        i = parts.index("branch")
        if i + 1 < len(parts):
            key = parts[i + 1]
    return key, node


def paint_color(paints):
    visible = [p for p in paints or [] if p.get("visible", True)]
    if len(visible) != 1 or visible[0].get("type") != "SOLID":
        return None
    p = visible[0]
    c = p["color"]
    values = [round(c.get(k, 0) * 255) for k in ("r", "g", "b")]
    values.append(round(c.get("a", 1) * p.get("opacity", 1) * 255))
    return "#" + "".join(f"{v:02x}" for v in values)


def flatten_frame(frame, key, factor):
    origin = frame["absoluteBoundingBox"]
    elements, annotations, node_ids = [], [], set()

    def visit(node, parent=None, visible=True):
        node_ids.add(node["id"])
        visible = visible and node.get("visible", True)
        if not visible:
            return
        for annotation in node.get("annotations", []):
            annotations.append({"node_id": node["id"], **annotation})
        box = node.get("absoluteBoundingBox")
        if node is not frame and box and box["width"] > 0 and box["height"] > 0:
            bbox = {"x": (box["x"] - origin["x"]) * factor, "y": (box["y"] - origin["y"]) * factor,
                    "width": box["width"] * factor, "height": box["height"] * factor}
            style = {}
            attrs = {"cornerRadius": "border_radius", "strokeWeight": "border_width",
                     "paddingTop": "padding_top", "paddingRight": "padding_right",
                     "paddingBottom": "padding_bottom", "paddingLeft": "padding_left", "itemSpacing": "gap"}
            for attr, prop in attrs.items():
                if attr == "strokeWeight" and not any(p.get("visible", True) for p in node.get("strokes", [])):
                    continue
                if attr.startswith("padding") or attr == "itemSpacing":
                    if node.get("layoutMode") not in ("HORIZONTAL", "VERTICAL", "GRID"):
                        continue
                if isinstance(node.get(attr), (int, float)):
                    style[prop] = node[attr] * factor
            color = paint_color(node.get("fills")) if node.get("opacity", 1) == 1 else None
            if color:
                style["color" if node["type"] == "TEXT" else "background_color"] = color
            if node["type"] == "TEXT" and not any(node.get("characterStyleOverrides", [])):
                text_style = node.get("style", {})
                if text_style.get("fontFamily"):
                    style["font_family"] = text_style["fontFamily"]
                for src, dest in (("fontSize", "font_size"), ("lineHeightPx", "line_height"), ("letterSpacing", "letter_spacing")):
                    if isinstance(text_style.get(src), (int, float)):
                        style[dest] = text_style[src] * factor
                if text_style.get("fontWeight") is not None:
                    style["font_weight"] = text_style["fontWeight"]
            elements.append({"id": node["id"], "name": node.get("name", ""), "type": node["type"],
                             "parent_id": parent, "bbox": bbox, "style": style,
                             "text": node.get("characters"),
                             "figma_url": f"https://www.figma.com/design/{key}?node-id={node['id'].replace(':', '-')}"})
        for child in node.get("children", []):
            visit(child, node["id"], visible)

    visit(frame)
    return elements, annotations, node_ids


def collect_figma(config, destination, base):
    key, node_id = parse_figma_url(config["url"])
    factor = finite_number(config.get("units_per_design_unit"), "design.units_per_design_unit", positive=True)
    token = os.environ.get(config.get("token_env", "FIGMA_TOKEN"))
    if not token:
        raise ReviewError("缺少 Figma 令牌环境变量。也可使用 MCP 导出的本地设计快照")
    headers = {"X-Figma-Token": token}
    data = http_get(f"https://api.figma.com/v1/files/{quote(key, safe='')}/nodes?" + urlencode({"ids": node_id}), headers)
    record = data.get("nodes", {}).get(node_id)
    if not record:
        raise ReviewError("Figma 节点不存在或不可访问")
    frame = record["document"]
    if frame.get("type") not in ("FRAME", "COMPONENT", "INSTANCE", "SECTION") or "absoluteBoundingBox" not in frame:
        raise ReviewError("请选择有明确边界的画板或组件，不要选择整个 Figma PAGE")
    elements, annotations, node_ids = flatten_frame(frame, key, factor)
    instructions = {"annotations": {"status": "read" if annotations else "empty", "items": annotations},
                    "measurements": {"status": "unavailable", "reason": "此 REST 采集器不读取 Measurement，请从 Figma 界面或 MCP 补充"}}
    try:
        all_comments = http_get(f"https://api.figma.com/v1/files/{quote(key, safe='')}/comments", headers).get("comments", [])
        roots = {c["id"] for c in all_comments if c.get("client_meta", {}).get("node_id") in node_ids}
        comments = [c for c in all_comments if c["id"] in roots or c.get("parent_id") in roots]
        unscoped = [c for c in all_comments if not c.get("parent_id") and not c.get("client_meta", {}).get("node_id")]
        instructions["comments"] = {"status": "read" if comments else "empty", "items": comments}
        if unscoped:
            instructions["comments"] = {"status": "unavailable", "items": comments,
                                         "reason": "文件中有未绑定节点的评论，需确认是否影响本画板"}
    except ReviewError:
        instructions["comments"] = {"status": "unavailable", "reason": "评论接口读取失败"}
    if config.get("instructions"):
        instructions.update(read_json(base / config["instructions"]))
    exported = http_get(f"https://api.figma.com/v1/images/{quote(key, safe='')}?" + urlencode({"ids": node_id, "format": "png", "scale": 1, "use_absolute_bounds": "true"}), headers)
    image_url = exported.get("images", {}).get(node_id)
    if not image_url:
        raise ReviewError("Figma 未返回设计图导出地址")
    image_path = destination / "design-source.png"
    image_path.write_bytes(http_get(image_url, binary=True))
    box = frame["absoluteBoundingBox"]
    snapshot = {"schema_version": 1, "coordinate_unit": "logical_px", "version": data.get("version") or data.get("lastModified"),
                "viewport": {"width": box["width"] * factor, "height": box["height"] * factor},
                "elements": elements, "instructions": instructions, "figma_file": key, "figma_node": node_id}
    return validate_snapshot(snapshot), image_path

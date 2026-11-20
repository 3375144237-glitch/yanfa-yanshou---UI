from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as SheetImage


def text(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else "" if value is None else str(value)


def safe_cell(value):
    value = text(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def evidence(design_image, actual_image, finding, destination):
    if not design_image or not actual_image:
        return None
    boxes = [finding["design_bbox"], finding["actual_bbox"]]
    padding = 24
    common_origin = finding["property"] in ("x", "y", "width", "height", "presence")
    left_edge = min(b["x"] for b in boxes)
    top_edge = min(b["y"] for b in boxes)
    width = int(max(b["x"] + b["width"] for b in boxes) - left_edge if common_origin else max(b["width"] for b in boxes)) + 2 * padding
    height = int(max(b["y"] + b["height"] for b in boxes) - top_edge if common_origin else max(b["height"] for b in boxes)) + 2 * padding
    canvas = Image.new("RGB", (width * 2 + 16, height), "#eef2f6")
    for index, (path, box) in enumerate(zip((design_image, actual_image), boxes)):
        with Image.open(path) as source:
            left, top = int((left_edge if common_origin else box["x"]) - padding), int((top_edge if common_origin else box["y"]) - padding)
            crop = source.convert("RGB").crop((left, top, left + width, top + height))
        if index == 1:
            x, y = box["x"] - left, box["y"] - top
            ImageDraw.Draw(crop).rectangle((x, y, min(width-2, x+box["width"]),
                                           min(height-2, y+box["height"])), outline="#d92d20", width=2)
        canvas.paste(crop, (index * (width + 16), 0))
    canvas.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    canvas.save(destination)
    return destination


def export(result, session, design_image=None, actual_image=None, *, xlsx=False):
    session = Path(session)
    evidence_dir = session / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    images = {}
    for finding in result["findings"]:
        path = evidence_dir / (finding["id"] + ".png")
        if evidence(design_image, actual_image, finding, path):
            images[finding["id"]] = path
            finding["evidence"] = str(path.relative_to(session))
    (session / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (session / "suggested-mappings.json").write_text(json.dumps(result["suggested_mappings"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if xlsx:
        write_xlsx(result, session / "issues.xlsx", images)


def write_xlsx(result, destination, images):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "问题汇总"
    sheet.append(["问题ID", "证据（设计｜运行）", "页面 / 状态", "平台", "确认程度", "元素 / 属性",
                  "设计值", "实际值", "差值", "修复建议", "设计链接", "运行定位", "处理状态", "备注", "优先级"])
    for finding in result["findings"]:
        row = [finding["id"], "", f"{result['case']} / {result['state']}", result["actual"]["platform"],
               "已确认差异" if finding["status"] == "confirmed" else "待确认候选",
               f"{finding['element']} / {finding['property']}", finding["expected"], finding["actual"],
               finding["delta"], finding["recommendation"], finding.get("figma_url"),
               finding.get("actual_id"), finding["workflow_status"], finding.get("note"), finding["priority"]]
        sheet.append([safe_cell(v) for v in row])
        image_path = images.get(finding["id"])
        if image_path:
            image = SheetImage(str(image_path))
            scale = min(300 / image.width, 120 / image.height, 1)
            image.width *= scale
            image.height *= scale
            sheet.add_image(image, f"B{sheet.max_row}")
            sheet.row_dimensions[sheet.max_row].height = 100
    coverage = workbook.create_sheet("覆盖与待确认")
    coverage.append(["元素", "属性", "结果", "映射已确认", "依据完整", "原因"])
    for c in result["checks"]:
        coverage.append([safe_cell(v) for v in (c["element"], c["property"], c["result"], c["mapping_confirmed"], c["basis_complete"], c["reason"])])
    retest = workbook.create_sheet("复验")
    retest.append(["问题ID", "元素", "属性", "复验结果"])
    for r in result["retest"]:
        retest.append([safe_cell(r.get(k)) for k in ("id", "element", "property", "result")])
    info = workbook.create_sheet("检查信息")
    info.append(["项目", "内容"])
    for key, value in [("用例", result["case"]), ("状态", result["state"]), ("设计版本", result["design"]["version"]),
                       ("单位", "logical_px；映射按项目确认，不把截图像素直接当作代码单位"),
                       ("覆盖", result["coverage"]), ("警告", result["warnings"]), ("运行条件", result["context"])]:
        info.append([key, safe_cell(value)])
    for ws in workbook:
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="153B42")
            cell.font = Font(color="FFFFFF", bold=True)
        for row in ws:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for col in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(col)].width = 28
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    sheet.column_dimensions["B"].width = 45
    sheet.column_dimensions["J"].width = 50
    info.column_dimensions["B"].width = 100
    workbook.save(destination)

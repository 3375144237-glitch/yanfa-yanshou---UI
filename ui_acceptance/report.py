from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as SheetImage


def text(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else "" if value is None else str(value)


def h(value):
    return html.escape(text(value), quote=True)


def safe_cell(value):
    value = text(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def data_image(path):
    if not path or not Path(path).is_file():
        return ""
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


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


def fix_prompt(result, finding):
    return (f"请修复 UI 差异 {finding['id']}。页面：{result['case']}；状态：{result['state']}。\n"
            f"元素：{finding['element']}；属性：{finding['property']}。\n"
            f"设计值：{text(finding['expected'])}；实际值：{text(finding['actual'])}；差值：{text(finding['delta'])}。\n"
            f"几何与尺寸单位：logical_px。运行定位：{finding.get('actual_id', '')}。\n"
            f"设计节点：{finding.get('figma_url', '')}。\n"
            f"来源提示（需在当前源码中核实）：{finding.get('source_hint') or '尚未定位源文件'}。\n"
            f"判断：{'已确认映射与设计依据' if finding['status']=='confirmed' else '候选，先核对映射和设计说明'}。\n"
            "修复后使用相同设计版本、设备与页面状态复验，同时检查受影响组件。")


def export(result, session, design_image=None, actual_image=None):
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
    write_xlsx(result, session / "issues.xlsx", images)
    write_html(result, session / "report.html", images, design_image, actual_image)


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


def write_html(result, destination, images, design_image=None, actual_image=None):
    confirmed = sum(f["status"] == "confirmed" for f in result["findings"])
    candidates = len(result["findings"]) - confirmed
    cards = []
    for f in result["findings"]:
        link = ""
        if urlsplit(f.get("figma_url", "")).scheme in ("https", "http"):
            link = f'<a href="{h(f["figma_url"])}" target="_blank" rel="noopener noreferrer">打开设计节点 ↗</a>'
        preview = data_image(images.get(f["id"]))
        img = f'<div class="pair-label"><span>设计稿</span><span>运行页面</span></div><img class="evidence" src="{preview}" alt="设计与运行局部对比，红框仅标注运行页面">' if preview else '<p class="muted">本项没有图像证据，需补充采集。</p>'
        cards.append(f'''<article class="issue" data-kind="{h(f['status'])}">
<div class="issue-top"><span class="badge {h(f['status'])}">{h(f['priority'])} · {"已确认差异" if f["status"]=="confirmed" else "待确认候选"}</span><code>{h(f["id"])}</code></div>
<h2>{h(f["element"])} <span class="muted">/ {h(f["property"])}</span></h2>
<div class="values"><div><small>设计值</small><strong>{h(f["expected"])}</strong></div><div><small>实际值</small><strong>{h(f["actual"])}</strong></div><div><small>差值</small><strong>{h(f["delta"]) or "—"}</strong></div></div>
{img}<p>{h(f["recommendation"])}</p>
<details><summary>定位与修复说明</summary><p>运行元素：<code>{h(f["actual_id"])}</code></p><p>源码提示：{h(f.get("source_hint")) or "尚未定位，需结合项目源码确认"}</p><textarea readonly aria-label="可复制的修复说明">{h(fix_prompt(result,f))}</textarea></details>
<div class="actions">{link}<button class="copy" type="button">复制修复说明</button><span class="muted">{h(f["workflow_status"])}</span></div></article>''')
    coverage_rows = "".join(f'<tr><td>{h(c["element"])}</td><td>{h(c["property"])}</td><td>{h(c["result"])}</td><td>{h(c["reason"]) or ("已确认" if c["mapping_confirmed"] and c["basis_complete"] else "映射或依据待确认")}</td></tr>' for c in result["checks"])
    retest_labels = {"resolved": "复验已消除", "still_present": "仍存在", "not_rechecked": "未完成复验"}
    retest_rows = "".join(f'<tr><td>{h(r["id"])}</td><td>{h(r["element"])}</td><td>{h(r["property"])}</td><td>{retest_labels[r["result"]]}</td></tr>' for r in result["retest"])
    warnings = "".join(f"<li>{h(w)}</li>" for w in result["warnings"])
    full_images = "".join(f'<figure><figcaption>{label}</figcaption><img src="{data_image(p)}" alt="{label}完整截图"></figure>' for label,p in (("设计稿",design_image),("运行页面",actual_image)) if p)
    cov = result["coverage"]
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(result["case"])} · UI 自查</title>
<style>
:root{{--ink:#17343b;--muted:#647780;--line:#dce6e7;--accent:#086b65}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f5;color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1180px;margin:auto;padding:48px 28px}}.eyebrow{{font-size:12px;letter-spacing:2px;color:var(--accent);font-weight:700}}h1{{font-size:34px;letter-spacing:-1px;margin:12px 0 6px}}h2{{font-size:20px;overflow-wrap:anywhere}}.muted,small{{color:var(--muted)}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}}.stat,.issue{{background:white;border:1px solid var(--line);border-radius:14px;padding:22px}}.stat strong{{display:block;font-size:30px}}.stat small{{display:block}}.warning{{background:#fff4dc;border:1px solid #ecd39b;padding:14px 20px;border-radius:10px;margin-bottom:20px}}.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:26px 0 18px}}button,input{{font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px 13px;background:white;color:var(--ink)}}button{{cursor:pointer}}button[aria-pressed="true"]{{background:var(--ink);color:white}}input{{margin-left:auto;min-width:230px}}.issues{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.issue-top,.actions{{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}}.issue-top code{{font-size:11px;color:var(--muted)}}.badge{{font-size:12px;padding:3px 10px;border-radius:20px}}.confirmed{{background:#fde8e6;color:#aa2920}}.candidate{{background:#fff2ce;color:#8c620b}}.values{{display:grid;grid-template-columns:repeat(3,1fr);background:#f5f8f8;border-radius:9px;padding:12px;margin:16px 0;gap:8px}}.values small,.values strong{{display:block}}.values strong{{overflow-wrap:anywhere}}.pair-label{{display:grid;grid-template-columns:1fr 1fr;font-size:12px;color:var(--muted);margin-bottom:5px}}.evidence{{width:100%;border:1px solid var(--line);border-radius:6px}}a{{color:var(--accent)}}textarea{{width:100%;height:190px;border:1px solid var(--line);border-radius:8px;padding:10px;font:12px/1.5 monospace}}details{{margin:15px 0}}summary{{cursor:pointer}}.actions{{margin-top:18px}}.full{{display:flex;gap:20px;align-items:flex-start;overflow:auto}}figure{{margin:0;flex:1;min-width:200px}}figure img{{max-width:100%;max-height:700px;object-fit:contain;object-position:top}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:white;font-size:13px}}th,td{{text-align:left;padding:9px;border-bottom:1px solid var(--line)}}.footer{{margin-top:30px;font-size:12px;color:var(--muted)}}[hidden]{{display:none!important}}@media(max-width:720px){{main{{padding:26px 16px}}.summary{{grid-template-columns:repeat(2,1fr)}}.issues{{grid-template-columns:1fr}}input{{width:100%;margin:8px 0 0}}h1{{font-size:28px}}}}
</style></head><body><main><div class="eyebrow">UI REVIEW / 开发自查</div><h1>{h(result["case"])}</h1><div class="muted">{h(result["state"])} · {h(result["actual"]["platform"])} · 设计版本 {h(result["design"]["version"])}</div>
<div class="summary"><div class="stat"><small>已确认差异</small><strong>{confirmed}</strong></div><div class="stat"><small>待确认候选</small><strong>{candidates}</strong></div><div class="stat"><small>已验证检查项</small><strong>{cov["verified_checks"]} / {cov["checks"]}</strong></div><div class="stat"><small>属性不可判定</small><strong>{cov["unknown"]}</strong></div></div>
{f'<div class="warning"><ul>{warnings}</ul></div>' if warnings else ''}
<details><summary>查看整页设计与运行截图</summary><div class="full">{full_images or "没有整页图片"}</div></details>
<div class="toolbar"><button class="filter" data-filter="all" aria-pressed="true">全部问题</button><button class="filter" data-filter="confirmed" aria-pressed="false">已确认</button><button class="filter" data-filter="candidate" aria-pressed="false">待确认</button><input id="search" aria-label="搜索元素或属性" placeholder="搜索元素、属性、问题 ID"></div>
<div class="issues">{''.join(cards) or '<p>当前没有已登记差异，请结合覆盖明细判断检查范围。</p>'}</div><p id="no-match" hidden>没有符合筛选条件的问题。</p>
<details><summary>检查覆盖明细（{cov["checks"]} 项）</summary><div class="table-wrap"><table><tr><th>元素</th><th>属性</th><th>结果</th><th>依据 / 原因</th></tr>{coverage_rows}</table></div></details>
<details><summary>上一轮问题复验（{len(result["retest"])} 项）</summary><div class="table-wrap"><table><tr><th>ID</th><th>元素</th><th>属性</th><th>本轮结果</th></tr>{retest_rows}</table></div></details>
<p class="footer">几何与尺寸使用 logical_px。覆盖统计仅表示已声明属性检查，不等于整页视觉验收通过。阴影、复杂图形、遮挡、图片内容及交互状态仍需结合证据复核。</p></main>
<script>
let filter='all';const cards=[...document.querySelectorAll('.issue')];const search=document.querySelector('#search');
function refresh(){{let visible=0;cards.forEach(c=>{{c.hidden=!((filter==='all'||c.dataset.kind===filter)&&c.textContent.toLowerCase().includes(search.value.toLowerCase()));if(!c.hidden)visible++;}});document.querySelector('#no-match').hidden=visible>0||cards.length===0;}}
document.querySelectorAll('.filter').forEach(b=>b.addEventListener('click',()=>{{filter=b.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));refresh();}}));search.addEventListener('input',refresh);
document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',async()=>{{const t=b.closest('.issue').querySelector('textarea');try{{await navigator.clipboard.writeText(t.value);b.textContent='已复制';}}catch{{t.closest('details').open=true;t.focus();t.select();b.textContent='请复制已选内容';}}}}));
</script></body></html>'''
    Path(destination).write_text(page, encoding="utf-8")

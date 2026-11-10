#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from ui_acceptance.collect import collect_native, collect_web, load_snapshot
from ui_acceptance.common import ReviewError, read_json, safe_name, validate_case, validate_snapshot, write_json
from ui_acceptance.engine import compare
from ui_acceptance.figma import collect_figma
from ui_acceptance.report import export


def normalize_image(source, snapshot, destination):
    if not source:
        return None
    with Image.open(source) as image:
        width = snapshot["viewport"]["width"]
        height = image.height * width / image.width
        if abs(height - snapshot["viewport"]["height"]) > 2:
            raise ReviewError("截图纵横比与声明的逻辑视口不一致，请核对裁切范围、系统栏与截图倍率")
        image.convert("RGB").resize((round(width), round(height)), Image.Resampling.LANCZOS).save(destination)
    return destination


def run_check(args):
    config_path = Path(args.config).resolve()
    base = config_path.parent
    case = validate_case(read_json(config_path))
    if args.only:
        case["scope"] = args.only.split(",")
    session = Path(args.out).resolve() if args.out else ROOT / "outputs/dev-review" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + safe_name(case["id"]))
    if session.exists():
        raise ReviewError("每轮检查使用新的输出目录，避免覆盖证据和复验基线")
    session.mkdir(parents=True)
    design_cfg = case["design"]
    design, design_source = load_snapshot(design_cfg, base) if design_cfg.get("snapshot") else collect_figma(design_cfg, session, base)
    actual_cfg = case["actual"]
    kind = actual_cfg.get("type")
    if kind == "snapshot":
        actual, actual_source = load_snapshot(actual_cfg, base)
    elif kind == "web":
        actual, actual_source = collect_web(actual_cfg, case.get("mappings", {}), session, base)
    elif kind == "native":
        actual, actual_source = collect_native(actual_cfg, case.get("mappings", {}), session)
    else:
        raise ReviewError("actual.type 需要 web、native 或 snapshot")
    design_image = normalize_image(design_source, design, session / "design.png")
    actual_image = normalize_image(actual_source, actual, session / "actual.png")
    write_json(session / "design.snapshot.json", design)
    write_json(session / "actual.snapshot.json", actual)
    previous = read_json(args.previous) if args.previous else None
    result = compare(case, design, actual, previous)
    export(result, session, design_image, actual_image)
    print(json.dumps({"report": str(session / "report.html"), "findings": len(result["findings"]), "coverage": result["coverage"]}, ensure_ascii=False))
    if args.strict:
        if any(f["status"] == "confirmed" for f in result["findings"]):
            return 1
        if not result["coverage"]["complete"]:
            return 3
    return 0


def run_demo(args):
    session = Path(args.out).resolve() if args.out else ROOT / "outputs" / ("demo-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    if session.exists():
        raise ReviewError("演示输出目录必须不存在")
    session.mkdir(parents=True)
    design = {"schema_version":1,"coordinate_unit":"logical_px","version":"synthetic-demo-v1",
              "viewport":{"width":360,"height":640},"instructions":{k:{"status":"empty"} for k in ("comments","annotations","measurements")},
              "elements":[{"id":"card","name":"课程卡片","bbox":{"x":16,"y":100,"width":328,"height":180},"style":{"padding_left":16,"border_radius":12}},
                          {"id":"title","name":"课程标题","bbox":{"x":32,"y":120,"width":220,"height":28},"style":{"font_size":20,"line_height":28}}]}
    actual = copy.deepcopy(design)
    actual.update(platform="demo", environment={"fixture":"synthetic"})
    actual["elements"][0]["bbox"]["x"] = 24
    actual["elements"][0]["bbox"]["width"] = 312
    actual["elements"][0]["style"]["padding_left"] = 24
    actual["elements"][1]["style"]["font_size"] = 16
    case = {"id":"课程详情 · 合成演示","state":"default","mappings":{e["id"]:{"actual_id":e["id"],"confirmed":True} for e in design["elements"]}}
    paths=[]
    for label, snapshot in (("design",design),("actual",actual)):
        image=Image.new("RGB",(360,640),"#edf3f3")
        draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,360,70),fill="#153b42")
        draw.text((20,25),"COURSE / UI REVIEW",fill="white")
        card=snapshot["elements"][0]["bbox"]
        draw.rounded_rectangle((card["x"],card["y"],card["x"]+card["width"],card["y"]+card["height"]),radius=12,fill="white")
        draw.text((card["x"]+16,120),"Design systems course",fill="#153b42")
        draw.rectangle((card["x"]+16,170,card["x"]+card["width"]-16,242),fill="#b9d8d2")
        path=session/(label+".png");image.save(path);paths.append(path)
        write_json(session/(label+".snapshot.json"),snapshot)
    result=compare(case,design,actual)
    result["warnings"].append("这是合成数据演示，用于体验报告与复验，不代表真实产品的验收结果")
    export(result,session,*paths)
    fixed=copy.deepcopy(design);fixed.update(platform="demo",environment={"fixture":"synthetic"})
    retest=compare(case,design,fixed,result)
    fixed_dir=session/"fixed";fixed_dir.mkdir()
    export(retest,fixed_dir,paths[0],paths[0])
    print(json.dumps({"report":str(session/"report.html"),"fixed_report":str(fixed_dir/"report.html")},ensure_ascii=False))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description="Web / 原生 App 研发 UI 自查")
    sub=parser.add_subparsers(dest="command",required=True)
    check=sub.add_parser("check",help="采集、检查、导出 HTML/JSON/Excel")
    check.add_argument("--config",required=True)
    check.add_argument("--out")
    check.add_argument("--previous",help="上一轮 review.json")
    check.add_argument("--only",help="仅复验指定设计节点 ID，逗号分隔")
    check.add_argument("--strict",action="store_true",help="已确认差异退出 1；覆盖不全退出 3")
    demo=sub.add_parser("demo",help="生成合成示例和修复后报告")
    demo.add_argument("--out")
    args=parser.parse_args(argv)
    try:
        return run_demo(args) if args.command=="demo" else run_check(args)
    except (ReviewError, OSError, ValueError, KeyError) as error:
        print(f"检查未完成：{error}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())

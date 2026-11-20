#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from ui_acceptance.collect import collect_native, collect_web, load_snapshot
from ui_acceptance.common import ReviewError, read_json, safe_name, validate_case, write_json
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
    export(result, session, design_image, actual_image, xlsx=args.xlsx)
    summary = {"review": str(session / "review.json"), "findings": len(result["findings"]), "coverage": result["coverage"]}
    if args.xlsx:
        summary["xlsx"] = str(session / "issues.xlsx")
    print(json.dumps(summary, ensure_ascii=False))
    if args.strict:
        if any(f["status"] == "confirmed" for f in result["findings"]):
            return 1
        if not result["coverage"]["complete"]:
            return 3
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description="页面实现完成后的 UI 验收")
    sub=parser.add_subparsers(dest="command",required=True)
    check=sub.add_parser("check",help="核对运行页面，生成 JSON 和截图证据")
    check.add_argument("--config",required=True)
    check.add_argument("--out")
    check.add_argument("--previous",help="上一轮 review.json")
    check.add_argument("--only",help="仅复验指定设计节点 ID，逗号分隔")
    check.add_argument("--strict",action="store_true",help="已确认差异退出 1；覆盖不全退出 3")
    check.add_argument("--xlsx",action="store_true",help="额外导出 Excel 验收汇总")
    args=parser.parse_args(argv)
    try:
        return run_check(args)
    except (ReviewError, OSError, ValueError, KeyError) as error:
        print(f"检查未完成：{error}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())

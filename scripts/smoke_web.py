#!/usr/bin/env python3
"""Real Chromium capture and regression round trip, using only local synthetic HTML."""
from __future__ import annotations
import argparse
import copy
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ui_acceptance.collect import collect_web
from ui_acceptance.common import write_json
from ui_acceptance.engine import compare
from ui_acceptance.report import export
from scripts.yanfa_review import normalize_image

parser=argparse.ArgumentParser()
parser.add_argument("--channel",help="Use installed chrome instead of Playwright Chromium")
parser.add_argument("--out",default="outputs/web-smoke")
args=parser.parse_args()
destination=ROOT/args.out
if destination.exists():
    raise SystemExit("Use a fresh --out")
destination.mkdir(parents=True)

shifted_mode = True
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        shifted=self.path == "/app" and shifted_mode
        document=f'''<!doctype html><meta charset="utf-8"><style>
        body{{margin:0;background:#eef4f4;font-family:Arial,sans-serif}}
        header{{height:70px;background:#153b42;color:white;padding:20px;box-sizing:border-box}}
        #card{{position:absolute;box-sizing:border-box;left:{24 if shifted else 16}px;top:100px;width:328px;height:180px;padding:16px;border-radius:12px;background:white}}
        h1{{font-size:20px;line-height:28px;margin:0}}p{{font-size:14px}}
        </style><header>COURSE / UI REVIEW</header><main id="card" data-ui-review="card" data-state="ready"><h1 id="title">Design systems course</h1><p>Build interfaces with precise spacing.</p></main>'''
        body=document.encode()
        self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass

server=HTTPServer(("127.0.0.1",0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    base={"type":"web","url":f"http://127.0.0.1:{server.server_port}/reference","viewport":{"width":360,"height":640},"dpr":2,"ready_selector":"[data-state=ready]"}
    if args.channel: base["channel"]=args.channel
    ref_dir=destination/"reference";ref_dir.mkdir()
    design,design_raw=collect_web(base,{},ref_dir,ROOT)
    design["elements"]=[e for e in design["elements"] if e.get("id") in ("#card","#title","[data-ui-review=\"card\"]")]
    design["version"]="synthetic-web-v1"
    design["instructions"]={k:{"status":"empty"} for k in ("comments","annotations","measurements")}
    # This binding is grounded in this intentionally constructed test fixture.
    mappings={e["id"]:{"selector":e["selector"],"confirmed":True,"properties":["x","y","width","height","font_size","padding_left"]} for e in design["elements"]}
    case={"id":"web-smoke","state":"default","mappings":mappings}
    shifted_dir=destination/"shifted";shifted_dir.mkdir()
    runtime=dict(base,url=f"http://127.0.0.1:{server.server_port}/app")
    actual,actual_raw=collect_web(runtime,mappings,shifted_dir,ROOT)
    result=compare(case,design,actual)
    assert result["findings"],"The injected 8px offset was missed"
    assert all(f["delta"]==8 and f["property"]=="x" for f in result["findings"]),result["findings"]
    assert all(f["status"]=="confirmed" for f in result["findings"])
    design_img=normalize_image(design_raw,design,destination/"design.png")
    actual_img=normalize_image(actual_raw,actual,destination/"actual.png")
    export(result,destination,design_img,actual_img)
    fixed_dir=destination/"fixed";fixed_dir.mkdir()
    shifted_mode=False
    fixed,fixed_raw=collect_web(runtime,mappings,fixed_dir,ROOT)
    verified=compare(case,design,fixed,result)
    assert not verified["findings"],verified["findings"]
    assert all(r["result"]=="resolved" for r in verified["retest"]),verified["retest"]
    fixed_img=normalize_image(fixed_raw,fixed,fixed_dir/"actual.png")
    export(verified,fixed_dir,design_img,fixed_img)
    write_json(destination/"design.snapshot.json",design)
    write_json(destination/"actual.snapshot.json",actual)
    print(json.dumps({"status":"passed","findings":len(result["findings"]),"resolved":len(verified["retest"]),"review":str(destination/"review.json")}))
finally:
    server.shutdown();server.server_close();thread.join()

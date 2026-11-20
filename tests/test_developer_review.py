import base64
import copy
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from openpyxl import load_workbook

from scripts.install_ui_skill import install
from scripts.yanfa_review import main as review_cli
from ui_acceptance.common import ReviewError, validate_snapshot
from ui_acceptance.collect import collect_native, parse_native_tree
from ui_acceptance.engine import compare
from ui_acceptance.figma import collect_figma, flatten_frame, parse_figma_url
from ui_acceptance.report import export


def snapshots():
    design = {"schema_version":1,"coordinate_unit":"logical_px","version":"v1",
              "viewport":{"width":360,"height":640},
              "instructions":{k:{"status":"empty"} for k in ("comments","annotations","measurements")},
              "elements":[{"id":"card","name":"card","bbox":{"x":16,"y":100,"width":328,"height":180},"style":{"padding_left":16}}]}
    actual = copy.deepcopy(design)
    actual.update(platform="web",environment={"browser":"test","dpr":1})
    case = {"id":"home","state":"default","mappings":{"card":{"actual_id":"card","confirmed":True}}}
    return case, design, actual


class ComparisonTests(unittest.TestCase):
    def test_eight_pixel_shift_is_detected_on_large_component(self):
        case, d, a = snapshots()
        a["elements"][0]["bbox"]["x"] += 8
        r = compare(case,d,a)
        self.assertEqual([(f["property"],f["delta"],f["status"]) for f in r["findings"]],[("x",8,"confirmed")])

    def test_auto_pairing_and_missing_instructions_are_not_confirmed(self):
        case,d,a=snapshots()
        case["mappings"]={}
        a["elements"][0]["bbox"]["x"]+=4
        self.assertEqual(compare(case,d,a)["findings"][0]["status"],"candidate")
        case["mappings"]={"card":{"actual_id":"card","confirmed":True}}
        d["instructions"]["measurements"]={"status":"unavailable"}
        self.assertEqual(compare(case,d,a)["findings"][0]["status"],"candidate")

    def test_duplicate_names_not_guessed(self):
        case,d,a=snapshots()
        case["mappings"]={}
        other=copy.deepcopy(a["elements"][0]);other["id"]="second"
        a["elements"].append(other)
        r=compare(case,d,a)
        self.assertFalse(r["findings"])
        self.assertTrue(all(c["result"]=="unknown" for c in r["checks"]))

    def test_unknown_native_style_is_not_zero(self):
        case,d,a=snapshots()
        a["elements"][0]["style"]={}
        r=compare(case,d,a)
        c=next(c for c in r["checks"] if c["property"]=="padding_left")
        self.assertEqual(c["result"],"unknown")
        self.assertFalse(r["coverage"]["complete"])

    def test_explicit_missing_component_differs_from_unmatched(self):
        case,d,a=snapshots()
        a["elements"]=[]
        a["mapping_queries"]={"card":"absent"}
        r=compare(case,d,a)
        self.assertEqual(r["findings"][0]["property"],"presence")
        self.assertEqual(r["findings"][0]["status"],"confirmed")
        case["mappings"]={}
        self.assertFalse(compare(case,d,a)["findings"])

    def test_unobserved_or_offscreen_match_does_not_prove_missing(self):
        case,d,a=snapshots()
        a["elements"]=[]
        for queries in ({}, {"card":"exists"}):
            a["mapping_queries"]=queries
            self.assertFalse(compare(case,d,a)["findings"])

    def test_unmeasured_issue_survives_multiple_retest_rounds(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["x"]+=8
        first=compare(case,d,a)
        no_measurement=copy.deepcopy(a)
        no_measurement["elements"]=[]
        second=compare(case,d,no_measurement,first)
        self.assertEqual(second["retest"][0]["result"],"not_rechecked")
        third=compare(case,d,no_measurement,second)
        self.assertEqual(third["retest"][0]["result"],"not_rechecked")
        a["elements"][0]["bbox"]["x"]=16
        final=compare(case,d,a,third)
        self.assertEqual(final["retest"][0]["result"],"resolved")

    def test_rebinding_to_another_node_cannot_close_old_issue(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["x"]+=8
        first=compare(case,d,a)
        a["elements"][0]["bbox"]["x"]=16
        a["elements"][0]["id"]="other"
        case["mappings"]["card"]["actual_id"]="other"
        self.assertEqual(compare(case,d,a,first)["retest"][0]["result"],"not_rechecked")

    def test_viewport_mismatch_does_not_generate_geometry_defects(self):
        case,d,a=snapshots()
        a["viewport"]["width"]=390
        a["elements"][0]["bbox"]["x"]+=5
        r=compare(case,d,a)
        self.assertFalse(r["findings"])
        self.assertIn("逻辑宽度",r["warnings"][0])

    def test_stable_identity_and_retest_dont_inherit_stale_measurements(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["x"]+=6
        first=compare(case,d,a)
        first["findings"][0]["workflow_status"]="修复中"
        first["findings"][0]["note"]="研发备注"
        a["elements"][0]["bbox"]["x"]+=2
        second=compare(case,d,a,first)
        self.assertEqual(first["findings"][0]["id"],second["findings"][0]["id"])
        self.assertEqual(second["findings"][0]["actual"],24)
        self.assertEqual(second["findings"][0]["note"],"研发备注")
        self.assertEqual(second["retest"][0]["result"],"still_present")
        a["elements"][0]["bbox"]["x"]=16
        fixed=compare(case,d,a,second)
        self.assertFalse(fixed["findings"])
        self.assertEqual(fixed["retest"][0]["result"],"resolved")

    def test_changed_baseline_environment_or_scope_cannot_close_issue(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["x"]+=6
        first=compare(case,d,a)
        a["elements"][0]["bbox"]["x"]=16
        for mutate in ("design","state","environment","scope","mapping"):
            c,dd,aa=copy.deepcopy((case,d,a))
            if mutate=="design": dd["elements"][0]["style"]["padding_left"]=15
            elif mutate=="state": c["state"]="other"
            elif mutate=="environment": aa["environment"]["dpr"]=2
            elif mutate=="scope": c["scope"]=["other"]
            else: c["mappings"]={}
            with self.subTest(mutate=mutate):
                self.assertEqual(compare(c,dd,aa,first)["retest"][0]["result"],"not_rechecked")

    def test_fixed_copy_only(self):
        case,d,a=snapshots()
        d["elements"][0]["text"]="确定";a["elements"][0]["text"]="确认"
        self.assertFalse(compare(case,d,a)["findings"])
        case["mappings"]["card"]["copy"]="fixed"
        self.assertEqual(compare(case,d,a)["findings"][0]["property"],"text")

    def test_partial_element_is_uncovered(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["y"]=600
        r=compare(case,d,a)
        self.assertFalse(r["findings"])
        self.assertFalse(r["coverage"]["complete"])

    def test_invalid_snapshot_ids_or_nan_rejected(self):
        _,d,_=snapshots()
        d["elements"].append(copy.deepcopy(d["elements"][0]))
        with self.assertRaises(ReviewError): validate_snapshot(d)
        d["elements"].pop();d["elements"][0]["bbox"]["x"]=float("nan")
        with self.assertRaises(ReviewError): validate_snapshot(d)


class NativeTests(unittest.TestCase):
    XML='<hierarchy><node bounds="[32,200][688,560]" resource-id="course.card" content-desc="card" text="Course"/><node bounds="[0,0][10,10]" displayed="false"/></hierarchy>'

    def test_android_and_ios_units_and_no_fabricated_style(self):
        elements=parse_native_tree(self.XML,{"tree_units_to_logical":0.5},{"card":{"resource_id":"course.card"}})
        self.assertEqual(elements[0]["bbox"],{"x":16,"y":100,"width":328,"height":180})
        self.assertEqual(elements[0]["style"],{})
        self.assertEqual(elements[0]["mapped_design_ids"],["card"])
        ios='<App><XCUIElementTypeButton x="16" y="10" width="80" height="32" name="buy"/></App>'
        self.assertEqual(parse_native_tree(ios,{"tree_units_to_logical":1},{})[0]["name"],"buy")
        with self.assertRaises(ReviewError):
            parse_native_tree(ios,{},{})

    def test_appium_only_reads_existing_session(self):
        calls=[]
        image=io.BytesIO();Image.new("RGB",(720,1280),"white").save(image,format="PNG")
        encoded=base64.b64encode(image.getvalue()).decode()
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                data={"value":encoded if self.path.endswith("/screenshot") else outer.XML}
                body=json.dumps(data).encode()
                self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(body)
            def log_message(self,*args): pass
        server=HTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                cfg={"server":f"http://127.0.0.1:{server.server_port}","session_id":"existing",
                     "viewport":{"width":360,"height":640},"tree_units_to_logical":0.5,"platform":"android","device":"test","runtime":"test"}
                snapshot,path=collect_native(cfg,{},Path(folder))
                self.assertTrue(path.is_file())
                self.assertEqual(snapshot["elements"][0]["bbox"]["x"],16)
                self.assertEqual(calls,["/session/existing/source","/session/existing/screenshot","/session/existing/source"])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_state_drift_rejected(self):
        cfg={"session_id":"existing","viewport":{"width":360,"height":640},"tree_units_to_logical":1}
        with tempfile.TemporaryDirectory() as folder, patch("ui_acceptance.collect.http_get",side_effect=[{"value":"a"},{"value":"unused"},{"value":"b"}]):
            with self.assertRaisesRegex(ReviewError,"变化"):
                collect_native(cfg,{},Path(folder))


class FigmaTests(unittest.TestCase):
    def test_rest_export_and_instruction_failure_remains_unknown(self):
        frame={"id":"1:2","name":"screen","type":"FRAME","absoluteBoundingBox":{"x":0,"y":0,"width":720,"height":1280},"children":[
            {"id":"1:3","type":"TEXT","name":"title","characters":"Hello","characterStyleOverrides":[0,0,0,0,0],
             "absoluteBoundingBox":{"x":32,"y":40,"width":200,"height":40},"style":{"fontSize":40},"strokeWeight":1,"strokes":[]}]}
        responses=[{"version":"v1","nodes":{"1:2":{"document":frame}}},ReviewError("HTTP 403"),{"images":{"1:2":"https://example.com/image"}},b"png"]
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ",{"FIGMA_TOKEN":"test-only"}), patch("ui_acceptance.figma.http_get",side_effect=responses):
            snapshot,path=collect_figma({"url":"https://www.figma.com/design/FILE?node-id=1-2","units_per_design_unit":0.5},Path(folder),Path(folder))
            self.assertEqual(snapshot["viewport"],{"width":360,"height":640})
            self.assertEqual(snapshot["instructions"]["comments"]["status"],"unavailable")
            self.assertEqual(snapshot["instructions"]["measurements"]["status"],"unavailable")
            self.assertEqual(snapshot["elements"][0]["style"]["font_size"],20)
            self.assertNotIn("border_width",snapshot["elements"][0]["style"])
            self.assertTrue(path.exists())

    def test_branch_url_and_hidden_descendants(self):
        self.assertEqual(parse_figma_url("https://www.figma.com/design/FILE/branch/BRANCH/Title?node-id=1-2"),("BRANCH","1:2"))
        frame={"id":"1","type":"FRAME","absoluteBoundingBox":{"x":100,"y":50,"width":720,"height":1280},"children":[
            {"id":"2","name":"hidden","type":"FRAME","visible":False,"children":[{"id":"3","type":"TEXT"}]},
            {"id":"4","name":"title","type":"TEXT","absoluteBoundingBox":{"x":132,"y":70,"width":200,"height":40},
             "characters":"Title","style":{"fontSize":40,"fontWeight":500},"annotations":[{"label":"fixed"}]}]}
        elements,annotations,ids=flatten_frame(frame,"FILE",0.5)
        self.assertEqual([e["id"] for e in elements],["4"])
        self.assertEqual(elements[0]["bbox"]["x"],16)
        self.assertEqual(elements[0]["style"]["font_size"],20)
        self.assertEqual(annotations[0]["node_id"],"4")


class InstallationTests(unittest.TestCase):
    def test_install_is_idempotent_and_preserves_active_project_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / ".git").mkdir()
            (project / "AGENTS.md").write_text("Base project rules\n", encoding="utf-8")
            override = project / "AGENTS.override.md"
            original = b"Active rules\r\nKeep the existing component library.\r\n"
            override.write_bytes(original)
            self.assertEqual(install(project), override.resolve())
            installed = override.read_bytes()
            self.assertTrue(installed.endswith(original))
            self.assertEqual((project / "AGENTS.md").read_text(), "Base project rules\n")
            skill = project / ".agents/skills/ui-implementation"
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertTrue((skill / "COPY-CLASSIFICATION.md").is_file())
            self.assertFalse((project / ".agents/skills/ui-acceptance").exists())
            install(project)
            self.assertEqual(override.read_bytes(), installed)

    def test_install_checks_conflicts_before_changing_project(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / ".git").mkdir()
            agents = project / "AGENTS.md"
            agents.write_bytes(b"Original rules\n")
            skill = project / ".agents/skills/ui-implementation"
            skill.mkdir(parents=True)
            reference = skill / "COPY-CLASSIFICATION.md"
            reference.write_bytes(b"Local customization\n")
            with self.assertRaisesRegex(ValueError, "手动合并"):
                install(project)
            self.assertEqual(agents.read_bytes(), b"Original rules\n")
            self.assertEqual(reference.read_bytes(), b"Local customization\n")
            self.assertFalse((skill / "SKILL.md").exists())


class ReportTests(unittest.TestCase):
    def test_check_cli_outputs_json_and_evidence_without_html_or_excel(self):
        case, design, actual = snapshots()
        actual["elements"][0]["bbox"]["x"] += 8
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "screen.png"
            Image.new("RGB", (360, 640), "white").save(image)
            for name, snapshot in (("design", design), ("actual", actual)):
                filename = name + ".json"
                (root / filename).write_text(json.dumps(snapshot), encoding="utf-8")
                case[name] = {"snapshot": filename, "image": image.name}
            case["actual"]["type"] = "snapshot"
            config = root / "case.json"
            config.write_text(json.dumps(case), encoding="utf-8")
            output = root / "acceptance"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = review_cli(["check", "--config", str(config), "--out", str(output), "--strict"])
            self.assertEqual(result, 1)
            review = output / "review.json"
            self.assertEqual(Path(json.loads(stdout.getvalue())["review"]), review.resolve())
            finding = json.loads(review.read_text(encoding="utf-8"))["findings"][0]
            self.assertTrue((output / finding["evidence"]).is_file())
            self.assertFalse(list(output.rglob("*.html")))
            self.assertFalse(list(output.rglob("*.xlsx")))

    def test_optional_excel_preserves_evidence_and_does_not_execute_formulas(self):
        case,d,a=snapshots()
        a["elements"][0]["bbox"]["x"]+=8
        r=compare(case,d,a)
        r["findings"][0]["note"]="=HYPERLINK(\"https://example.com\")"
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            image=p/"image.png";Image.new("RGB",(360,640),"white").save(image)
            export(r,p,image,image,xlsx=True)
            self.assertFalse(list(p.rglob("*.html")))
            self.assertTrue((p/"review.json").exists())
            sheet=load_workbook(p/"issues.xlsx")["问题汇总"]
            self.assertNotEqual(sheet["N2"].data_type,"f")
            self.assertTrue(sheet["N2"].value.startswith("'="))
            self.assertEqual(len(sheet._images),1)


if __name__=="__main__":
    unittest.main()

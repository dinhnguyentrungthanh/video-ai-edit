import ast
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from biliflow import export_dialog
from biliflow.control_center import _dashboard_html
from biliflow.export_dialog import (
    EXPORT_CUSTOM_GB_ATTRIBUTES,
    EXPORT_DIALOG_JS,
    EXPORT_GATE_MESSAGE,
    EXPORT_SIZE_OPTIONS,
    export_size_options_html,
)
from biliflow.review_workflow import _interactive_html


class ExportDialogTests(unittest.TestCase):
    """The card's "Xuất video" and the review page share one export dialog."""

    def setUp(self):
        self.review = _interactive_html("token")
        self.dashboard = _dashboard_html()

    def test_both_pages_embed_the_same_dialog_code(self):
        for name, page in (("review", self.review), ("dashboard", self.dashboard)):
            with self.subTest(page=name):
                self.assertEqual(page.count(EXPORT_DIALOG_JS), 1)
                self.assertEqual(page.count("function exportSizeSelection("), 1)
                self.assertEqual(page.count("function exportConfirmText("), 1)
                self.assertIn(EXPORT_CUSTOM_GB_ATTRIBUTES, page)
                self.assertNotIn("__EXPORT", page)
        # The review page uses the shared functions instead of its own copies.
        self.assertIn(
            "function outputSizeSelection(){return exportSizeSelection($('#output-size-mode').value,"
            "$('#custom-output-gb').value);}", self.review,
        )
        self.assertIn("alert(EXPORT_GATE_MESSAGE)", self.review)
        self.assertIn("if(!confirm(exportConfirmText(selection)))return;", self.review)
        self.assertIn("const choice=exportPolicyChoice(queue.export_size_policy);", self.review)
        self.assertNotIn("Khóa các lựa chọn hiện tại", self.review.replace(EXPORT_DIALOG_JS, ""))
        # The dashboard card posts the selection to the same finalize route.
        self.assertIn("post(`/api/jobs/${id}/review/finalize`,selection)", self.dashboard)
        self.assertIn("alert(EXPORT_GATE_MESSAGE)", self.dashboard)
        self.assertIn("if(!confirm(exportConfirmText(selection))){release();return}", self.dashboard)
        self.assertNotIn("Khóa các lựa chọn hiện tại", self.dashboard.replace(EXPORT_DIALOG_JS, ""))

    def test_review_page_markup_is_unchanged(self):
        self.assertEqual(
            export_size_options_html(),
            '<option value="default">Tối đa 3,5 GB (mặc định)</option><option value="custom">'
            'Giới hạn tùy chỉnh</option><option value="unlimited">Không giới hạn dung lượng</option>',
        )
        self.assertIn(
            '<select id="output-size-mode" onchange="toggleCustomOutputSize()">'
            + export_size_options_html() + "</select>", self.review,
        )
        self.assertIn(
            '<input id="custom-output-gb" type="number" min="0.05" max="1000" step="0.1" value="3.5">',
            self.review,
        )
        self.assertEqual(EXPORT_GATE_MESSAGE, "Vẫn còn mục chưa có quyết định cuối cùng.")
        self.assertEqual([value for value, _label in EXPORT_SIZE_OPTIONS], ["default", "custom", "unlimited"])

    def test_module_has_no_imports(self):
        tree = ast.parse(Path(export_dialog.__file__).read_text(encoding="utf-8"))
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_selection_confirm_and_options_in_node(self):
        script = EXPORT_DIALOG_JS + """
const out={};
out.default=exportSizeSelection('default','');
out.unlimited=exportSizeSelection('unlimited','');
out.custom=exportSizeSelection('custom','2.5');
for(const bad of ['0.01','1001','','abc']){try{exportSizeSelection('custom',bad);out['bad_'+bad]=null}catch(e){out['bad_'+bad]=e.message}}
out.confirm=exportConfirmText(out.custom);
out.options=Object.fromEntries(['','default','custom','unlimited'].map(m=>[m,exportSizeOptionsHtml(m)]));
out.choices=[exportPolicyChoice(null),exportPolicyChoice({mode:'custom',maximum_output_gb:2}),
  exportPolicyChoice({mode:'custom',maximum_output_gb:0}),exportPolicyChoice({mode:'unlimited'}),exportPolicyChoice({mode:'weird'})];
out.gate=EXPORT_GATE_MESSAGE;
console.log(JSON.stringify(out));
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["default"], {"size_mode": "default", "description": "tối đa 3,5 GB"})
        self.assertEqual(out["unlimited"], {"size_mode": "unlimited", "description": "không giới hạn dung lượng"})
        self.assertEqual(out["custom"], {"size_mode": "custom", "max_output_gb": 2.5, "description": "tối đa 2,5 GB"})
        for bad in ("0.01", "1001", "", "abc"):
            self.assertEqual(out["bad_" + bad], "Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.")
        self.assertEqual(out["confirm"], "Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 2,5 GB)?")
        for mode, html in out["options"].items():
            self.assertEqual(html, export_size_options_html(mode), mode)
        self.assertEqual(out["choices"], [
            {"mode": "default", "gb": 3.5}, {"mode": "custom", "gb": 2}, {"mode": "custom", "gb": 3.5},
            {"mode": "unlimited", "gb": 3.5}, {"mode": "default", "gb": 3.5},
        ])
        self.assertEqual(out["gate"], EXPORT_GATE_MESSAGE)


class InlineScriptSyntaxTests(unittest.TestCase):
    """Every inline <script> of both pages must parse; one SyntaxError blanks the whole page."""

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_every_inline_script_parses_in_node(self):
        pages = (("review", _interactive_html("token")), ("dashboard", _dashboard_html()))
        with tempfile.TemporaryDirectory() as directory:
            for name, page in pages:
                scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.S)
                self.assertTrue(scripts, name)
                for index, script in enumerate(scripts):
                    with self.subTest(page=name, script=index):
                        path = Path(directory) / f"{name}-{index}.js"
                        path.write_text(script, encoding="utf-8")
                        result = subprocess.run(
                            [shutil.which("node"), "--check", str(path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Export dialog logic shared by the review page and the dashboard job card.

Both pages embed the same text, so the size choice, its validation, the
confirmation and the "every item decided" gate are identical wherever the user
clicks "Xuất video". The module has no imports: it is page text only, imported
by review_workflow.py and control_center.py, and is in no scan stage cache key.
"""

EXPORT_GATE_MESSAGE = "Vẫn còn mục chưa có quyết định cuối cùng."

# (value, label) of the size choices; the first one is the default.
EXPORT_SIZE_OPTIONS = (
    ("default", "Tối đa 3,5 GB (mặc định)"),
    ("custom", "Giới hạn tùy chỉnh"),
    ("unlimited", "Không giới hạn dung lượng"),
)

# Attributes of the custom maximum field (GB); the server validates the same range.
EXPORT_CUSTOM_GB_ATTRIBUTES = 'type="number" min="0.05" max="1000" step="0.1"'
EXPORT_CUSTOM_GB_DEFAULT = "3.5"


def export_size_options_html(selected: str = "") -> str:
    """The <option> list; ``selected`` marks one value (none for the static review page)."""
    return "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{label}</option>'
        for value, label in EXPORT_SIZE_OPTIONS
    )


EXPORT_DIALOG_JS = (
    f"const EXPORT_GATE_MESSAGE='{EXPORT_GATE_MESSAGE}';"
    "const EXPORT_SIZE_OPTIONS=["
    + ",".join(f"['{value}','{label}']" for value, label in EXPORT_SIZE_OPTIONS)
    + "],EXPORT_SIZE_MODES=EXPORT_SIZE_OPTIONS.map(x=>x[0]);\n"
    "function exportSizeOptionsHtml(selected){return EXPORT_SIZE_OPTIONS.map(([value,label])=>"
    "`<option value=\"${value}\"${value===selected?' selected':''}>${label}</option>`).join('');}\n"
    "function exportSizeSelection(mode,customGb){if(mode==='unlimited')return{size_mode:'unlimited',"
    "description:'không giới hạn dung lượng'};if(mode==='default')return{size_mode:'default',"
    "description:'tối đa 3,5 GB'};const maximum=Number(customGb);if(!Number.isFinite(maximum)||"
    "maximum<0.05||maximum>1000)throw new Error('Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.');"
    "return{size_mode:'custom',max_output_gb:maximum,description:`tối đa ${maximum.toLocaleString('vi-VN')} GB`};}\n"
    "function exportConfirmText(selection){return `Khóa các lựa chọn hiện tại và bắt đầu xuất video "
    "hoàn chỉnh (${selection.description})?`;}\n"
    "function exportPolicyChoice(policy){const value=policy||{},mode=EXPORT_SIZE_MODES.includes(value.mode)?"
    "value.mode:'default',gb=Number(value.maximum_output_gb);return{mode,gb:mode==='custom'&&gb>0?gb:"
    f"{EXPORT_CUSTOM_GB_DEFAULT}}};}}\n"
)

# -*- coding: utf-8 -*-
"""独立冒烟自检 —— 故意只依赖标准库。

由 subtitle_app.py 在任何第三方 / GUI 导入之前调用，也供打包出的 exe 使用：
    SubtitleGenerator.exe --selftest

这样即使冻结产物缺了 tkinter 等依赖，也能立刻写出结果并退出，
不会弹出无人点击的错误对话框、不会把 CI 卡死。
"""

import json
import os
import sys

MODULES = (
    "tkinter", "tkinterdnd2", "faster_whisper", "ctranslate2",
    "av", "onnxruntime", "tokenizers", "huggingface_hub", "numpy",
)


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _ensure_tcl():
    """部分 venv 会丢失 Tcl 资源路径；从官方 Python 安装目录推导补全。

    冻结产物由 PyInstaller 的 _tkinter runtime hook 负责，不在这里干预。
    """
    if getattr(sys, "frozen", False):
        return
    base = getattr(sys, "base_prefix", sys.prefix)
    tcl_dir = os.path.join(base, "tcl", "tcl8.6")
    tk_dir = os.path.join(base, "tcl", "tk8.6")
    if os.path.isdir(tcl_dir) and "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = tcl_dir
        os.environ["TK_LIBRARY"] = tk_dir


def run():
    import importlib
    _ensure_tcl()
    checks = {}

    for name in MODULES:
        try:
            mod = importlib.import_module(name)
            checks[name] = str(getattr(mod, "__version__", "ok"))
        except Exception as e:
            checks[name] = f"FAIL: {type(e).__name__}: {e}"

    # Tcl 解释器不需要显示器，CI 上也能跑；足以验证 tcl/tk 数据是否随产物打包。
    try:
        import tkinter
        checks["tcl_init"] = "Tcl " + str(tkinter.Tcl().call("info", "patchlevel"))
    except Exception as e:
        checks["tcl_init"] = f"FAIL: {type(e).__name__}: {e}"

    ok = not any(str(v).startswith("FAIL") for v in checks.values())
    result = {
        "ok": ok,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "app_dir": _app_dir(),
        "checks": checks,
    }

    out = os.path.join(_app_dir(), "_selftest_result.json")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        result["write_error"] = str(e)
        ok = False
    try:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())

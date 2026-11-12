# -*- coding: utf-8 -*-
"""把本项目打包成 Windows 可执行程序。

本地开发机和 GitHub Actions 共用这一份脚本，避免两边打包参数不同步：
    python build.py
产物：dist/SubtitleGenerator/SubtitleGenerator.exe

注意：模型文件不打包进 exe（体积数百 MB ~ 数 GB），由程序首次运行时下载。
"""

import os
import sys
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
NAME = "SubtitleGenerator"          # 用 ASCII 名，避免 CI / 压缩环节出现编码问题

COLLECT_DATA = ["faster_whisper", "tkinterdnd2", "ctranslate2", "onnxruntime"]
COLLECT_BINARIES = ["ctranslate2", "av", "onnxruntime", "tokenizers"]
COLLECT_SUBMODULES = ["huggingface_hub"]
HIDDEN_IMPORTS = [
    "faster_whisper", "ctranslate2", "av", "onnxruntime",
    "tokenizers", "huggingface_hub", "tkinterdnd2", "tkinter", "selftest",
]
COPY_METADATA = ["faster_whisper", "huggingface_hub", "tokenizers",
                 "onnxruntime", "ctranslate2", "av"]


def verify(exe):
    """运行冻结后的 exe --selftest，确认依赖在打包环境里没有丢失。"""
    import json
    workdir = os.path.dirname(exe)
    result_file = os.path.join(workdir, "_selftest_result.json")
    if os.path.exists(result_file):
        os.remove(result_file)

    print("\n正在自检打包产物（首次解包较慢，约 30-60 秒）...", flush=True)
    try:
        proc = subprocess.run([exe, "--selftest"], cwd=workdir, timeout=120)
    except Exception as e:
        print(f"[ERROR] 无法运行自检：{e}")
        return 1

    if not os.path.exists(result_file):
        print(f"[ERROR] 自检未生成结果文件（退出码 {proc.returncode}）")
        print("        常见原因：当前解释器不是官方完整版 Python，")
        print("        PyInstaller 没能把 tkinter 打进产物。")
        print("        请用干净的官方 Python 重建构建环境后重试。")
        return 1

    with open(result_file, encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if not data.get("ok", False):
        print("[ERROR] 打包产物自检未通过。")
        return 1
    print("\n自检通过，产物可分发。")
    return 0


def ensure_tcl_env():
    """PyInstaller 若判定 Tcl/Tk 数据不可用，会主动把整个 tkinter 排除掉，
    产出的 exe 会报 ModuleNotFoundError。部分 venv 下需要显式补设路径。
    """
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return
    base = getattr(sys, "base_prefix", sys.prefix)
    for tcl_dir, tk_dir in (
        (os.path.join(base, "tcl", "tcl8.6"), os.path.join(base, "tcl", "tk8.6")),
        (os.path.join(base, "lib", "tcl8.6"), os.path.join(base, "lib", "tk8.6")),
    ):
        if os.path.isfile(os.path.join(tcl_dir, "init.tcl")) and os.path.isdir(tk_dir):
            os.environ.setdefault("TCL_LIBRARY", tcl_dir)
            os.environ.setdefault("TK_LIBRARY", tk_dir)
            print(f"[info] 已补设 Tcl/Tk 数据路径：{tcl_dir}")
            return
    print("[info] 使用解释器自带的 Tcl/Tk 路径配置。")


def rotate_old_outputs():
    """把上次产物改名保留，而不是让 PyInstaller 递归删掉整个目录。

    既留了一份可回退的版本，也避开部分环境对批量删除的限制。
    """
    import time
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for rel in (os.path.join("dist", NAME), os.path.join("build", NAME)):
        path = os.path.join(BASE, rel)
        if os.path.isdir(path):
            backup = f"{path}_prev_{stamp}"
            try:
                os.rename(path, backup)
                print(f"[info] 旧产物已保留为 {os.path.basename(backup)}")
            except Exception as e:
                print(f"[WARN] 旧产物改名失败（不影响构建）：{e}")


def build():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[ERROR] PyInstaller 未安装，请先运行：pip install pyinstaller")
        return 1

    ensure_tcl_env()
    rotate_old_outputs()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",            # onedir 启动快、依赖可见、便于排查
        "--noconsole",         # 桌面软件不弹黑色控制台
        "--name", NAME,
        "--distpath", os.path.join(BASE, "dist"),
        "--workpath", os.path.join(BASE, "build"),
        "--specpath", os.path.join(BASE, "build"),
    ]
    # 显式把官方 Python 的标准库路径交给分析器。某些 venv 下 PyInstaller
    # 找不到 tkinter，会打出 "ModuleNotFoundError: No module named tkinter" 的坏 exe。
    import sysconfig
    stdlib = sysconfig.get_paths().get("stdlib", "")
    if stdlib:
        for p in (stdlib, os.path.join(os.path.dirname(stdlib), "DLLs")):
            if os.path.isdir(p):
                cmd += ["--paths", p]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in COLLECT_DATA:
        cmd += ["--collect-data", mod]
    for mod in COLLECT_BINARIES:
        cmd += ["--collect-binaries", mod]
    for mod in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", mod]
    for mod in COPY_METADATA:
        cmd += ["--copy-metadata", mod]
    cmd.append(os.path.join(BASE, "subtitle_app.py"))

    print("打包命令：")
    print("  " + " ".join(cmd[:8]) + " ...")
    print(f"共 {len(cmd)} 个参数，开始构建（首次约 1-3 分钟）...\n", flush=True)

    result = subprocess.run(cmd, cwd=BASE)
    if result.returncode != 0:
        print("\n[ERROR] PyInstaller 构建失败。")
        return result.returncode

    exe = os.path.join(BASE, "dist", NAME, NAME + ".exe")
    if not os.path.isfile(exe):
        print(f"\n[ERROR] 未找到预期产物：{exe}")
        return 1

    print("\n构建成功！")
    print(f"  主程序：{exe}")
    print(f"  发布目录：{os.path.join(BASE, 'dist', NAME)}")
    print("  验证命令：SubtitleGenerator.exe --selftest")
    print("\n提示：分发时请把整个 SubtitleGenerator 文件夹一起打包，")
    print("      不要只复制单个 exe（依赖 DLL 都在同目录）。")
    if "--skip-verify" in sys.argv:
        print("\n（按参数要求跳过产物自检）")
        return 0
    return verify(exe)


if __name__ == "__main__":
    sys.exit(build())

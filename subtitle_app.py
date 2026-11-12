# -*- coding: utf-8 -*-
"""
本地视频字幕生成器 / Local Video Subtitle Generator
--------------------------------------------------
基于 faster-whisper (OpenAI Whisper 的 CTranslate2 加速实现)，全程本地运行，
视频不上传、无需 API Key、无需付费。

导入 MP4 等视频 -> 语音识别 -> 导出 SRT / VTT / TXT 字幕（可压制内嵌字幕视频）
"""

import os
import sys

# --selftest 必须在任何第三方 / GUI 导入之前处理：这样即使打包产物缺了
# tkinter 也能秒退并写出结果，不会弹出无人点击的对话框卡住 CI。
if __name__ == "__main__" and "--selftest" in sys.argv:
    import selftest
    sys.exit(selftest.run())

import queue
import shutil
import threading
import traceback
import subprocess
from datetime import datetime

import zh_text  # 中文输出后处理：繁转简 / 全角标点

# PyInstaller 冻结后 __file__ 指向临时解包目录，模型缓存会随每次启动丢失，
# 因此打包运行时一律以 exe 所在目录为基准。
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 国内访问 Hugging Face 的镜像，必须在使用 huggingface_hub 之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 模型缓存放在软件目录内，方便备份 / 迁移，重装系统也不丢
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = os.path.join(APP_DIR, "models")

# pythonw.exe 启动时没有控制台，stdout/stderr 为 None，
# 第三方库里的 print 会抛异常，这里统一重定向到日志文件
if sys.stdout is None or sys.stderr is None:
    _log_fp = open(os.path.join(APP_DIR, "运行日志.log"), "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fp
    if sys.stderr is None:
        sys.stderr = _log_fp

# 某些精简版 Python 创建的 venv 会丢失 Tcl/Tk 资源路径，导致 Tk() 报
# "Can't find a usable init.tcl"。从 base_prefix 推导补全，正常环境不受影响。
try:
    _tcl_dir = os.path.join(sys.base_prefix, "tcl", "tcl8.6")
    _tk_dir = os.path.join(sys.base_prefix, "tcl", "tk8.6")
    if os.path.isdir(_tcl_dir) and "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = _tcl_dir
        os.environ["TK_LIBRARY"] = _tk_dir
except Exception:
    pass

# faster-whisper 1.2.x 调用 av.open(..., metadata_errors="ignore")，
# 但 PyAV 17+ 移除了该参数，会抛 TypeError 导致无法解码任何视频。
# 这里做一层自动降级：先原样调用，仅在不识别该参数时去掉重试。
try:
    import av
    _av_open_orig = av.open

    def _av_open_compat(*args, **kwargs):
        try:
            return _av_open_orig(*args, **kwargs)
        except TypeError as _e:
            if "metadata_errors" in str(_e) and "metadata_errors" in kwargs:
                kwargs.pop("metadata_errors")
                return _av_open_orig(*args, **kwargs)
            raise

    av.open = _av_open_compat
except Exception:
    pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# 拖放支持是可选增强：装了 tkinterdnd2 就启用，没装也不影响使用
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:
    HAS_DND = False

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm",
              ".m4v", ".ts", ".mpg", ".mpeg", ".wav", ".mp3", ".m4a", ".flac")

MODELS = {
    "tiny   ·  约 75 MB   ·  最快，准确率一般": "tiny",
    "base   ·  约 145 MB  ·  轻量，速度很快": "base",
    "small  ·  约 480 MB  ·  推荐，速度与准确率均衡": "small",
    "medium ·  约 1.5 GB  ·  更准，CPU 上较慢": "medium",
    "large-v3 · 约 3 GB · 最准，CPU 上很慢": "large-v3",
}

LANGUAGES = {
    "自动检测": None,
    "中文（普通话）": "zh",
    "English": "en",
    "日本語": "ja",
    "한국어": "ko",
    "粤语": "yue",
    "Français": "fr",
    "Deutsch": "de",
    "Español": "es",
    "Русский": "ru",
}

ZH_PROMPT = "以下是普通话的句子，请使用简体中文并加上合适的标点符号。"


def fmt_time(t, sep=","):
    """秒 -> SRT/VTT 时间戳 HH:MM:SS,mmm"""
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def parse_dnd_paths(data):
    """解析 tkinterdnd2 返回的路径，处理 {含空格的路径} 形式"""
    out, buf, depth = [], "", 0
    for ch in data:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == " " and depth == 0:
            if buf:
                out.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        out.append(buf)
    return out


class SubtitleApp:
    def __init__(self, root):
        self.root = root
        self.root.title("本地视频字幕生成器  ·  MP4 → SRT / VTT / TXT")
        self.root.geometry("860x790")
        self.root.minsize(760, 680)

        self.msg_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.files = []

        self._build_ui()
        self.root.after(100, self._poll_messages)
        self._log("准备就绪。导入视频 → 选择模型 → 点击「开始生成」。")
        if not HAS_DND:
            self._log("提示：安装 tkinterdnd2 后可支持把文件直接拖进窗口。")
        self._log("首次使用需要在线下载所选模型（已配置国内镜像），之后完全离线可用。")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 9), foreground="#5b6470")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="本地视频字幕生成器", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(outer, text="视频不上传 · 无需账号与 API Key · 基于 Whisper 本地语音识别",
                  style="Sub.TLabel").pack(anchor=tk.W, pady=(2, 10))

        # ---------- 文件列表 ----------
        lf = ttk.Labelframe(outer, text=" 1  导入视频 ", padding=8)
        lf.pack(fill=tk.BOTH, expand=True)

        bar = ttk.Frame(lf)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="添加视频", command=self.add_files).pack(side=tk.LEFT)
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side=tk.LEFT)
        ttk.Button(bar, text="清空", command=self.clear_files).pack(side=tk.LEFT, padx=6)
        self.count_var = tk.StringVar(value="共 0 个文件")
        ttk.Label(bar, textvariable=self.count_var, style="Sub.TLabel").pack(side=tk.RIGHT)

        list_frame = ttk.Frame(lf)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=6,
                                  font=("Consolas", 9), yscrollcommand=scroll.set,
                                  activestyle="dotbox", relief=tk.SOLID, borderwidth=1)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.listbox.yview)
        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)

        # ---------- 设置 ----------
        sf = ttk.Labelframe(outer, text=" 2  识别与输出设置 ", padding=8)
        sf.pack(fill=tk.X, pady=(10, 0))
        sf.columnconfigure(1, weight=1)
        sf.columnconfigure(3, weight=1)

        ttk.Label(sf, text="识别模型：").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.model_var = tk.StringVar(value=list(MODELS.keys())[2])
        ttk.Combobox(sf, textvariable=self.model_var, values=list(MODELS.keys()),
                     state="readonly").grid(row=0, column=1, columnspan=3, sticky=tk.EW, pady=4)

        ttk.Label(sf, text="视频语言：").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.lang_var = tk.StringVar(value="自动检测")
        ttk.Combobox(sf, textvariable=self.lang_var, values=list(LANGUAGES.keys()),
                     state="readonly", width=20).grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(sf, text="计算精度：").grid(row=1, column=2, sticky=tk.W, padx=(14, 0), pady=4)
        self.compute_var = tk.StringVar(value="int8（CPU 推荐）")
        ttk.Combobox(sf, textvariable=self.compute_var,
                     values=["int8（CPU 推荐）", "int8_float32", "float32（更准更慢）"],
                     state="readonly", width=22).grid(row=1, column=3, sticky=tk.W, pady=4)

        fmt = ttk.Frame(sf)
        fmt.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 4))
        ttk.Label(fmt, text="导出格式：").pack(side=tk.LEFT)
        self.fmt_srt = tk.BooleanVar(value=True)
        self.fmt_vtt = tk.BooleanVar(value=False)
        self.fmt_txt = tk.BooleanVar(value=True)
        ttk.Checkbutton(fmt, text="SRT 字幕", variable=self.fmt_srt).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(fmt, text="VTT 网页字幕", variable=self.fmt_vtt).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(fmt, text="TXT 纯文本", variable=self.fmt_txt).pack(side=tk.LEFT, padx=(12, 0))
        self.burn_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fmt, text="压制内嵌字幕 MP4", variable=self.burn_var).pack(side=tk.LEFT, padx=(12, 0))

        self.vad_var = tk.BooleanVar(value=True)
        self.show_text_var = tk.BooleanVar(value=True)
        self.zh_norm_var = tk.BooleanVar(value=True)
        opt = ttk.Frame(sf)
        opt.grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(2, 4))
        ttk.Checkbutton(opt, text="过滤静音片段（推荐，减少幻听）", variable=self.vad_var).pack(side=tk.LEFT)
        ttk.Checkbutton(opt, text="实时显示识别出的文字", variable=self.show_text_var).pack(side=tk.LEFT, padx=(16, 0))
        self.source_var = tk.StringVar(value="国内镜像（推荐）")
        ttk.Label(opt, text="  模型源：").pack(side=tk.LEFT)
        ttk.Combobox(opt, textvariable=self.source_var,
                     values=["国内镜像（推荐）", "官方源（海外）"],
                     state="readonly", width=16).pack(side=tk.LEFT, padx=4)

        opt2 = ttk.Frame(sf)
        opt2.grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(2, 2))
        ttk.Checkbutton(opt2, text="中文输出规范化（繁体→简体、全角标点，推荐）",
                        variable=self.zh_norm_var).pack(side=tk.LEFT)
        ttk.Label(opt2, text="仅对识别为中文的字幕生效",
                  style="Sub.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        out = ttk.Frame(sf)
        out.grid(row=5, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        ttk.Label(out, text="输出目录：").pack(side=tk.LEFT)
        self.outdir_var = tk.StringVar(value="")
        self.outdir_entry = ttk.Entry(out, textvariable=self.outdir_var)
        self.outdir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(out, text="浏览…", command=self.choose_outdir).pack(side=tk.LEFT)
        ttk.Label(sf, text="（留空 = 字幕保存在视频同目录；批量时建议指定统一目录）",
                  style="Sub.TLabel").grid(row=6, column=0, columnspan=4, sticky=tk.W)

        # ---------- 执行 ----------
        af = ttk.Frame(outer)
        af.pack(fill=tk.X, pady=(12, 6))
        self.start_btn = ttk.Button(af, text="▶  开始生成字幕", style="Accent.TButton",
                                    command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(af, text="■  停止", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(af, text="打开输出目录", command=self.open_outdir).pack(side=tk.LEFT)
        ttk.Button(af, text="使用说明", command=self.show_help).pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="等待任务")
        ttk.Label(outer, textvariable=self.status_var, style="Sub.TLabel").pack(anchor=tk.W, pady=(3, 6))

        ttk.Label(outer, text="运行日志", style="Sub.TLabel").pack(anchor=tk.W)
        self.log = scrolledtext.ScrolledText(outer, height=9, font=("Consolas", 9),
                                             relief=tk.SOLID, borderwidth=1, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.configure(state=tk.DISABLED)

    # -------------------------------------------------------------- 文件操作
    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for path in self.files:
            self.listbox.insert(tk.END, path)
        self.count_var.set(f"共 {len(self.files)} 个文件")

    def _add(self, paths):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                for base, _, names in os.walk(p):
                    for n in names:
                        if n.lower().endswith(VIDEO_EXTS):
                            full = os.path.join(base, n)
                            if full not in self.files:
                                self.files.append(full)
                                added += 1
            elif p.lower().endswith(VIDEO_EXTS) and os.path.isfile(p) and p not in self.files:
                self.files.append(p)
                added += 1
        self._refresh_list()
        if added:
            self._log(f"已添加 {added} 个文件。")

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[("视频 / 音频文件", "*.mp4 *.mov *.mkv *.avi *.flv *.wmv *.webm "
                                     "*.m4v *.ts *.mpg *.wav *.mp3 *.m4a *.flac"),
                       ("MP4 视频", "*.mp4"), ("所有文件", "*.*")])
        if paths:
            self._add(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="选择文件夹（递归扫描其中的视频）")
        if d:
            self._add([d])

    def on_drop(self, event):
        self._add(parse_dnd_paths(event.data))

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
        self._refresh_list()

    def clear_files(self):
        self.files.clear()
        self._refresh_list()

    def choose_outdir(self):
        d = filedialog.askdirectory(title="选择字幕输出目录")
        if d:
            self.outdir_var.set(d)

    def open_outdir(self):
        d = self.outdir_var.get().strip()
        if not d and self.files:
            d = os.path.dirname(self.files[0])
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "还没有可用的输出目录，请先生成一次字幕。")

    # ---------------------------------------------------------------- 日志
    def _emit(self, kind, payload=None):
        self.msg_queue.put((kind, payload))

    def _log(self, text):
        self._emit("log", text)

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log.configure(state=tk.NORMAL)
                    stamp = datetime.now().strftime("%H:%M:%S")
                    self.log.insert(tk.END, f"[{stamp}] {payload}\n")
                    self.log.see(tk.END)
                    self.log.configure(state=tk.DISABLED)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    self.progress["value"] = payload
                elif kind == "finished":
                    self._set_running(False)
                    ok, total = payload
                    self.status_var.set(f"完成：成功 {ok} / 共 {total}")
                    if ok:
                        messagebox.showinfo("完成", f"字幕生成完毕！\n成功 {ok} 个 / 共 {total} 个。")
                elif kind == "error":
                    self._set_running(False)
                    self.status_var.set("出错，详见日志")
                    messagebox.showerror("出错了", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _set_running(self, running):
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        if not running:
            self.stop_event.clear()

    # ---------------------------------------------------------------- 流程
    def start(self):
        if not self.files:
            messagebox.showwarning("还没导入视频", "请先点击「添加视频」或把 mp4 拖进列表。")
            return
        if not (self.fmt_srt.get() or self.fmt_vtt.get() or self.fmt_txt.get() or self.burn_var.get()):
            messagebox.showwarning("未选择输出", "请至少勾选一种导出格式。")
            return
        outdir = self.outdir_var.get().strip()
        if outdir:
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("输出目录不可用", str(e))
                return
        self._set_running(True)
        self.progress["value"] = 0
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self._emit("status", "正在停止…（当前片段处理完后中断）")

    def _run(self):
        total = len(self.files)
        ok = 0
        try:
            endpoint = ("https://huggingface.co"
                        if self.source_var.get().startswith("官方")
                        else "https://hf-mirror.com")
            os.environ["HF_ENDPOINT"] = endpoint
            # 国内镜像对 hf_xet 协议支持不佳，改走普通 HTTP 下载更稳；
            # 官方源则保留 xet 加速。
            if endpoint.startswith("https://huggingface.co"):
                os.environ.pop("HF_HUB_DISABLE_XET", None)
            else:
                os.environ["HF_HUB_DISABLE_XET"] = "1"

            try:
                from faster_whisper import WhisperModel
            except ImportError:
                self._emit("error", "缺少识别引擎。请先双击「启动.bat」（首次会自动安装），或手动运行 install.bat。")
                return

            # huggingface_hub 在模块导入时缓存了 endpoint，这里同步一次，保证切换立即生效
            try:
                import huggingface_hub.constants as _hfc
                for _attr in ("_HF_ENDPOINT", "ENDPOINT"):
                    if hasattr(_hfc, _attr):
                        setattr(_hfc, _attr, endpoint)
            except Exception:
                pass

            model_name = MODELS[self.model_var.get()]
            compute = self.compute_var.get().split("（")[0].strip()
            language = LANGUAGES[self.lang_var.get()]

            self._emit("status", f"正在加载模型 {model_name} …")
            self._log(f"加载模型：{model_name}（{compute}）　下载源：{endpoint}")
            self._log("首次使用需下载模型，请耐心等待；下载进度可在控制台/日志中查看。")
            model = WhisperModel(model_name, device="cpu", compute_type=compute)
            self._log("模型加载完成。")

            for idx, path in enumerate(self.files):
                if self.stop_event.is_set():
                    self._log("已被用户中断。")
                    break
                try:
                    if self._transcribe_one(model, path, language, idx, total):
                        ok += 1
                except Exception as e:
                    self._log(f"✗ 处理失败：{os.path.basename(path)} -> {e}")
                    self._log(traceback.format_exc().splitlines()[-1])

            self._emit("progress", 100)
            self._log(f"全部结束：成功 {ok} / 共 {total} 个。")
            self._emit("finished", (ok, total))
        except Exception as e:
            self._log("致命错误：\n" + traceback.format_exc())
            self._emit("error", str(e))

    def _transcribe_one(self, model, path, language, idx, total):
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        outdir = self.outdir_var.get().strip() or os.path.dirname(os.path.abspath(path))
        os.makedirs(outdir, exist_ok=True)

        self._emit("status", f"[{idx + 1}/{total}] 正在识别：{name}")
        self._log(f"── [{idx + 1}/{total}] {name}")

        kwargs = dict(
            language=language,
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=self.vad_var.get(),
        )
        if self.vad_var.get():
            kwargs["vad_parameters"] = {"min_silence_duration_ms": 500}
        if language == "zh":
            kwargs["initial_prompt"] = ZH_PROMPT

        segments, info = model.transcribe(path, **kwargs)
        detected = getattr(info, "language", None) or "?"
        duration = float(getattr(info, "duration", 0) or 0)
        self._log(f"   检测语言：{detected}　时长：{duration:.1f} 秒")

        # Whisper 中文输出常混入繁体、半角标点，这里按检测到的语言自动规范化
        zh_norm = self.zh_norm_var.get() and detected == "zh"
        zh_conv_at_start = zh_text.has_full_converter()
        if zh_norm and not zh_conv_at_start:
            self._log("   ⚠ 未安装 zhconv：仅能规范标点，重跑「安装依赖.bat」可启用繁体转简体")

        srt_lines, vtt_lines, txt_lines = [], ["WEBVTT", ""], []
        count = 0
        last_pct = -1

        for seg in segments:
            if self.stop_event.is_set():
                break
            text = (seg.text or "").strip()
            if not text:
                continue
            if zh_norm:
                text = zh_text.normalize_zh(text)
            count += 1
            start, end = float(seg.start), float(seg.end)
            srt_lines.append(f"{count}\n{fmt_time(start)} --> {fmt_time(end)}\n{text}\n")
            vtt_lines.append(f"{fmt_time(start, '.')} --> {fmt_time(end, '.')}\n{text}\n")
            txt_lines.append(text)
            if self.show_text_var.get():
                self._log(f"   {fmt_time(start)[:-4]}  {text}")

            if duration > 0:
                pct = int(min(99, (idx + min(1.0, end / duration)) / total * 100))
                if pct != last_pct:
                    self._emit("progress", pct)
                    last_pct = pct

        if zh_norm and zh_conv_at_start and not zh_text.has_full_converter():
            self._log("   ⚠ 繁简词典不可用，已跳过繁转简（标点规范化仍生效）")

        if count == 0:
            self._log("   ⚠ 未识别到语音。请确认视频含音轨，或换更大的模型。")
            return False

        written = []
        if self.fmt_srt.get():
            p = os.path.join(outdir, stem + ".srt")
            with open(p, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write("\n".join(srt_lines) + "\n")
            written.append(os.path.basename(p))
        if self.fmt_vtt.get():
            p = os.path.join(outdir, stem + ".vtt")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(vtt_lines) + "\n")
            written.append(os.path.basename(p))
        if self.fmt_txt.get():
            p = os.path.join(outdir, stem + ".txt")
            with open(p, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write("\n".join(txt_lines) + "\n")
            written.append(os.path.basename(p))
        if self.burn_var.get():
            srt_path = os.path.join(outdir, stem + ".srt")
            if not os.path.exists(srt_path):
                with open(srt_path, "w", encoding="utf-8-sig", newline="\n") as f:
                    f.write("\n".join(srt_lines) + "\n")
            if self._burn(path, srt_path, os.path.join(outdir, stem + "_字幕版.mp4")):
                written.append(stem + "_字幕版.mp4")

        self._log(f"   ✓ 共 {count} 条字幕 → {', '.join(written)}")
        return True

    def _burn(self, video, srt, output):
        """用 ffmpeg 把 SRT 压制进视频（借助临时 ASCII 路径规避 Windows 路径转义问题）"""
        try:
            import tempfile
            with tempfile.TemporaryDirectory(prefix="subburn_") as tmp:
                shutil.copyfile(srt, os.path.join(tmp, "sub.srt"))
                vf = ("subtitles=sub.srt:force_style="
                      "'FontName=Microsoft YaHei,FontSize=22,PrimaryColour=&H00FFFFFF,"
                      "OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=24'")
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-i", os.path.abspath(video), "-vf", vf,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                       "-c:a", "copy", os.path.abspath(output)]
                r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    self._log(f"   ⚠ 字幕压制失败（字幕文件不受影响）：{(r.stderr or '').strip()[:300]}")
                    return False
            return True
        except FileNotFoundError:
            self._log("   ⚠ 未找到 ffmpeg，跳过字幕压制。字幕文件已正常生成。")
            return False
        except Exception as e:
            self._log(f"   ⚠ 字幕压制失败：{e}")
            return False

    # ---------------------------------------------------------------- 帮助
    def show_help(self):
        help_text = """【第一次使用】
1. 双击「启动.bat」；首次会自动调用 install.bat 安装依赖（只需一次）。
   也可手动双击 install.bat，网络慢时脚本会自动切换到国内镜像。
2. 第一次运行会下载所选模型，下载一次之后即可完全离线使用。
   国内保持「国内镜像」，海外用户把「模型源」切到「官方源（海外）」。
3. 需要官方版 Python 3.10+（安装时勾选 tcl/tk），精简版 Python 缺少图形组件。

【怎么用】
· 点「添加视频」或直接把 mp4 拖进列表，支持一次导入多个批量处理。
· 模型选择：想快选 tiny/base；中文日常视频推荐 small；追求准确率选 medium / large-v3。
· 语言默认「自动检测」；纯中文视频手动选「中文（普通话）」会更准、标点更规范。
· 勾选需要的格式：
    SRT —— 通用字幕，剪映 / Premiere / PotPlayer / VLC 都认
    VTT —— 网页视频字幕
    TXT —— 只要文字内容、做会议纪要或文案稿
    压制内嵌字幕 MP4 —— 直接烧进画面、发平台用（需要 ffmpeg，会重新编码稍慢）
· 输出目录留空时，字幕自动保存在视频同目录、同名。

【速度与准确率】
CPU 上 small 模型处理 10 分钟视频大约需要 2-6 分钟，取决于机器性能。
视频越清晰、人声越干净、背景音乐越小，效果越好。
识别结果属于 AI 转写，人名、专业术语、方言建议人工校对一遍。

【隐私】
识别 100% 在本机完成，视频不会上传到任何服务器。仅下载模型时需要联网。

【常见问题】
· 提示「缺少识别引擎」→ 运行「安装依赖.bat」。
· 模型下载卡住 → 检查网络；或手动把模型文件夹放到本软件的 models 目录。
· 未识别到语音 → 确认视频有音轨、不是纯音乐；换个更大的模型。
· 中文出现繁体 → 语言手动选「中文（普通话）」，程序会自动引导简体输出。
"""
        win = tk.Toplevel(self.root)
        win.title("使用说明")
        win.geometry("660x560")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        txt = scrolledtext.ScrolledText(frm, font=("Microsoft YaHei UI", 10), wrap=tk.WORD,
                                        relief=tk.SOLID, borderwidth=1)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert(tk.END, help_text)
        txt.configure(state=tk.DISABLED)
        ttk.Button(frm, text="知道了", command=win.destroy).pack(pady=(10, 0))


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:
        icon = os.path.join(APP_DIR, "app.ico")
        if os.path.exists(icon):
            root.iconbitmap(icon)
    except Exception:
        pass
    SubtitleApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _err = traceback.format_exc()
        try:
            with open(os.path.join(APP_DIR, "错误.txt"), "w", encoding="utf-8") as _f:
                _f.write(_err)
        except Exception:
            pass
        try:
            _r = tk.Tk()
            _r.withdraw()
            messagebox.showerror("启动失败", "程序无法启动，详情已写入「错误.txt」。\n\n" + _err[-600:])
        except Exception:
            pass

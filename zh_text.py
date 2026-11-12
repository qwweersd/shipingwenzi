# -*- coding: utf-8 -*-
"""中文识别结果的后处理：繁体转简体、半角标点转全角、去掉汉字间的空格。

Whisper 的中文输出有两个常见问题：
  1. 同一段音频里可能忽然整段变成繁体（tiny 模型尤其明显）；
  2. 标点一律用半角 "," ":"，中文排版应该用 "，" "："。

本模块只依赖标准库；装了 zhconv 时做完整繁简转换，没装时**只**做标点与
空格规范化（绝不用手工映射表猜字，避免把字改错）。
"""
import re

try:
    import zhconv
    _HAS_ZHCONV = True
except Exception:                                    # pragma: no cover - 可选依赖
    zhconv = None
    _HAS_ZHCONV = False

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SPACE_IN_CJK = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")
_PUNCT = {",": "，", ":": "：", ";": "；", "?": "？", "!": "！"}


_ZHCONV_BROKEN = False     # 打包漏收 zhcdict.json 时置位，避免每条字幕都重试/抛错


def has_full_converter():
    """是否装了完整可用的繁简转换库（供日志/自检使用）。"""
    return _HAS_ZHCONV and not _ZHCONV_BROKEN


def to_simplified(text):
    """繁体 -> 简体；缺库或词典数据缺失时原样返回（降级，不影响字幕生成）。"""
    global _ZHCONV_BROKEN
    if not has_full_converter() or not text:
        return text
    try:
        return zhconv.convert(text, "zh-cn")
    except Exception:                                    # 常见于冻结产物漏收数据文件
        _ZHCONV_BROKEN = True
        return text


def fix_punctuation(text):
    """只把「挨着汉字」的半角标点换成全角，避免误伤 1,000 / 3.14 这类数字。"""
    if not text or not _CJK.search(text):
        return text
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in _PUNCT:
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if _CJK.match(prev or " ") or _CJK.match(nxt or " "):
                chars[i] = _PUNCT[ch]
    return "".join(chars)


def normalize_zh(text, simplified=True, punctuation=True):
    """中文规范化总入口。非中文文本原样返回。"""
    if not text or not _CJK.search(text):
        return text
    out = text.strip()
    if simplified:
        out = to_simplified(out)
    out = _SPACE_IN_CJK.sub("", out)
    if punctuation:
        out = fix_punctuation(out)
    return out

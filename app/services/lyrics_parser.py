"""歌词解析服务（LRC/QRC/假名注音）。

从 `app.ui.main_window_helpers` 下沉的纯解析逻辑，无 Qt 依赖。
包含数据类 FuriganaAnnotation/LyricWord/LyricEntry 与全部 LRC/QRC/假名注音解析函数。
符号名（含下划线前缀）保持原样，通过 `main_window_helpers` 兼容再导出，现有 UI 导入路径不变。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_KANA_RE = re.compile(r"^\[kana:(.*)\]$", re.MULTILINE)


@dataclass(slots=True)
class FuriganaAnnotation:
    char_index: int
    text: str


@dataclass(slots=True)
class LyricWord:
    text: str
    start_ms: int
    duration_ms: int


@dataclass(slots=True)
class LyricEntry:
    timestamp: float
    original: str = ""
    romaji: str = ""
    translation: str = ""
    original_words: list[LyricWord] = field(default_factory=list)
    romaji_words: list[LyricWord] = field(default_factory=list)
    furigana: list[FuriganaAnnotation] = field(default_factory=list)

    def line_count(self, *, show_japanese: bool = True, show_romaji: bool = True) -> int:
        """计算需要显示的文本行数。

        此方法根据传入的参数以及对象自身存储的内容，
        计算最终输出时需要占用的行数。计算结果确保至少为一行。
        例如，可以选择是否包含日文原文及其注音、罗马字转写和翻译。

        Args:
            show_japanese (bool): 是否在计算中包含日文原文行。默认为 True。
            show_romaji (bool): 是否在计算中包含罗马字转写行。默认为 True。

        Returns:
            int: 计算得出的显示行数，最小值为 1。
        """
        count = 0
        # 如果需要显示日文，且原始日文内容存在，则需要一行来显示
        if show_japanese and self.original:
            count += 1
            # 如果日文内容有注音（furigana），则需要额外一行来显示注音
            if self.furigana:
                count += 1
        # 如果需要显示罗马字，且罗马字内容存在，则需要一行来显示
        if show_romaji and self.romaji:
            count += 1
        # 如果翻译内容存在，则需要一行来显示翻译
        if self.translation:
            count += 1
        # 确保最终返回的行数至少为 1
        return max(1, count)

    def display_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        """
        功能：显示文本，包括日语、罗马音和翻译。根据参数决定显示哪些内容，如果没有内容则返回默认值或"♪"。
        参数：
            show_japanese (bool): 是否显示日文原文，默认为True。
            show_romaji (bool): 是否显示罗马音，默认为True。
        返回值：
            str: 显示的文本，由换行符连接各部分；如果没有内容，则返回第一个可用文本或"♪"。
        """
        parts: list[str] = []  # 初始化一个空列表，用于存储要显示的文本部分
        if show_japanese and self.original:  # 如果需要显示日语且存在日文原文
            parts.append(self.original)  # 将日文原文添加到列表
        if show_romaji and self.romaji:  # 如果需要显示罗马音且存在罗马音
            parts.append(self.romaji)  # 将罗马音添加到列表
        if self.translation:  # 如果存在翻译
            parts.append(self.translation)  # 将翻译添加到列表
        if not parts:  # 如果列表为空（即没有添加任何文本部分）
            return self.original or self.romaji or self.translation or "♪"  # 返回第一个可用的文本，如果都没有则返回"♪"
        return "\n".join(parts)  # 用换行符连接所有文本部分并返回

    def compact_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        """
        功能：根据参数和对象属性返回压缩后的文本。
        参数：
            show_japanese (bool): 是否显示日文，默认为True。
            show_romaji (bool): 是否显示罗马音，默认为True。
        返回值：str，返回原文、罗马音、翻译或默认符号"♪"。
        """
        if show_japanese and self.original:
            # 如果启用日文且原文存在，返回原文
            return self.original
        if show_romaji and self.romaji:
            # 如果启用罗马音且罗马音存在，返回罗马音
            return self.romaji
        if self.translation:
            # 如果翻译存在，返回翻译
            return self.translation
        # 回退选项：尝试返回原文、罗马音、翻译或默认符号
        return self.original or self.romaji or self.translation or "♪"


def _format_time(sec: float) -> str:
    """格式化时间为MM:SS或HH:MM:SS格式.

    Args:
        sec: 秒数。

    Returns:
        str: 格式化后的时间字符串。
    """
    total = max(0, int(sec))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_lrc_time(sec: float) -> str:
    """格式化LRC歌词时间戳为MM:SS格式.

    Args:
        sec: 秒数。

    Returns:
        str: 格式化后的时间字符串（MM:SS格式）。
    """
    safe = max(0.0, float(sec))
    total = int(safe)
    minutes = total // 60
    seconds = total % 60
    return f"{minutes:02d}:{seconds:02d}"


def _parse_lrc_entries(raw: str) -> list[tuple[float, str]]:
    """解析LRC歌词文件内容.

    从LRC格式的文本中提取时间戳和歌词文本。

    Args:
        raw: LRC格式的原始文本内容。

    Returns:
        list[tuple[float, str]]: 包含（时间戳（秒），歌词文本）的列表，按时间顺序排序。
    """
    result: list[tuple[float, str]] = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        matches = list(_LRC_RE.finditer(line))
        if not matches:
            continue

        text = html.unescape(_LRC_RE.sub("", line).strip())
        for m in matches:
            mm = int(m.group(1))
            ss = int(m.group(2))
            frac_raw = m.group(3) or "0"
            if len(frac_raw) == 1:
                ms = int(frac_raw) * 100
            elif len(frac_raw) == 2:
                ms = int(frac_raw) * 10
            else:
                ms = int(frac_raw[:3])

            sec = mm * 60 + ss + (ms / 1000.0)
            result.append((sec, text))

    result.sort(key=lambda x: x[0])
    return result


_QRC_LINE_RE = re.compile(r"\[(\d+),(\d+)\]([^\[]*)")
_QRC_WORD_RE = re.compile(r"\((\d+),(\d+)\)")
_QRC_KANA_GROUP_RE = re.compile(r"(\d)(?:(?:\(\d+,\d+\))?[\u3040-\u309f\u30a0-\u30ff]*)*")
_QRC_FURIGANA_BASE_RE = re.compile(r"[\u4e00-\u9fff\uff10-\uff19\uff21-\uff5a\u3005]")


def _parse_qrc_words(text_raw: str) -> list[LyricWord]:
    """解析QRC格式的原始歌词文本，提取其中的歌词词组。

    该函数利用预定义的正则表达式从给定的原始字符串中匹配所有歌词词组，
    并将它们转换为`LyricWord`对象组成的列表。

    Args:
        text_raw (str): 包含QRC格式歌词信息的原始字符串。

    Returns:
        list[LyricWord]: 一个列表，其中每个元素都是一个`LyricWord`对象，
        包含了词组的文本内容、开始时间（毫秒）和持续时间（毫秒）。
    """
    words: list[LyricWord] = []  # 初始化一个空列表，用于存储解析出的歌词词组
    # 使用预定义的正则表达式对象在原始文本中迭代查找所有匹配项
    text_start = 0
    for m in _QRC_WORD_RE.finditer(text_raw):
        # 将每个匹配到的子组（词组文本、开始时间、持续时间）转换为 LyricWord 对象
        # 并添加到列表中。注意：时间值从字符串转换为整数。
        text = text_raw[text_start : m.start()]
        if text:
            words.append(LyricWord(text=text, start_ms=int(m.group(1)), duration_ms=int(m.group(2))))
        text_start = m.end()
    return words  # 返回解析完成的歌词词组列表


def _parse_qrc_entries(raw: str) -> list[tuple[float, str]]:
    """解析 QRC 格式的歌词原始文本，提取时间戳和对应的歌词内容。

    Args:
        raw (str): QRC 格式的歌词原始文本，可能是纯文本或 XML 格式。

    Returns:
        list[tuple[float, str]]: 解析后的歌词列表，每个元素是一个元组，
            包含歌词时间戳（秒，浮点数）和对应的歌词文本（字符串），
            列表按时间戳升序排序。
    """
    content = raw.strip()
    # 检查内容是否为 XML 格式（以特定标签开头）
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(content)
            # 遍历 XML 树，查找包含 LyricContent 属性的元素
            for lyric_elem in root.iter():
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    content = lc  # 将 content 替换为提取到的歌词内容
                    break
        except Exception:
            pass  # 如果 XML 解析失败，则忽略并继续使用原始 content

    result: list[tuple[float, str]] = []
    # 使用正则表达式匹配 QRC 歌词行（时间戳和歌词内容）
    for m in _QRC_LINE_RE.finditer(content):
        start_ms = int(m.group(1))  # 提取起始时间（毫秒）
        text_raw = m.group(3)  # 提取原始歌词文本
        # 移除歌词中的逐字标记（通过正则替换），并去除首尾空格
        text = _QRC_WORD_RE.sub("", text_raw).strip()
        if not text:
            continue  # 跳过空歌词行
        sec = start_ms / 1000.0  # 将毫秒转换为秒
        result.append((sec, text))
    result.sort(key=lambda x: x[0])  # 按时间戳升序排序
    return result


def _parse_qrc_structured(raw: str, *, is_romaji: bool = False) -> list[tuple[float, str, list[LyricWord]]]:
    """解析QRC格式歌词字符串，返回结构化的歌词数据列表。

    将原始QRC歌词字符串解析为按时间排序的元组列表，每个元组包含：
    - 起始时间（秒，浮点数）
    - 歌词文本（去除时间标签后的纯文本）
    - 歌词单词列表（由LyricWord对象组成）

    如果输入是XML格式的QRC数据，会先提取其中的LyricContent字段。

    Args:
        raw (str): 原始QRC格式歌词字符串。
        is_romaji (bool): 是否为罗马音歌词（此参数在当前实现中未使用）。

    Returns:
        list[tuple[float, str, list[LyricWord]]]: 结构化歌词列表，
        按时间顺序排列，每个元素为(时间秒数, 歌词文本, 单词列表)的元组。
    """
    # 移除字符串首尾空白字符
    content = raw.strip()

    # 检查是否为XML格式的QRC数据
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        # 导入XML解析库
        import xml.etree.ElementTree as ET

        try:
            # 解析XML内容
            root = ET.fromstring(content)
            # 遍历XML树寻找包含歌词内容的元素
            for lyric_elem in root.iter():
                # 获取LyricContent属性
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    # 找到歌词内容，替换content变量
                    content = lc
                    break
        except Exception:
            # XML解析失败，保持原content不变
            pass

    # 初始化结果列表，类型注解为包含(浮点数, 字符串, LyricWord列表)的元组列表
    result: list[tuple[float, str, list[LyricWord]]] = []

    # 使用正则表达式匹配所有歌词行
    for m in _QRC_LINE_RE.finditer(content):
        # 提取起始时间（毫秒）
        start_ms = int(m.group(1))
        # 提取原始文本内容（包含时间标签和歌词）
        text_raw = m.group(3)
        # 移除文本中的时间标签并清理空白
        text = _QRC_WORD_RE.sub("", text_raw).strip()

        # 跳过空文本行
        if not text:
            continue

        # 解析歌词单词
        words = _parse_qrc_words(text_raw)
        # 将毫秒转换为秒
        sec = start_ms / 1000.0
        # 将解析结果添加到结果列表
        result.append((sec, text, words))

    # 按时间顺序排序结果
    result.sort(key=lambda x: x[0])
    return result


def _detect_lyrics_format(raw: str) -> str:
    """检测歌词文本的格式类型。

    通过分析原始字符串的内容特征，判断歌词格式是QRC格式还是LRC格式。

    参数:
        raw (str): 原始歌词文本字符串

    返回值:
        str: 格式标识字符串，"qrc" 或 "lrc"
    """
    # 移除字符串首尾的空白字符
    stripped = raw.strip()

    # 检查是否以XML声明或QrcInfos标签开头（QRC格式特征）
    if stripped.startswith("<?xml") or stripped.startswith("<QrcInfos"):
        return "qrc"

    # 检查原始字符串中是否包含QRC文件扩展名特征
    if "_qm.qrc" in raw or "_qmRoma.qrc" in raw or "_qmts.qrc" in raw:
        return "qrc"

    # 使用预定义的正则表达式检查前500个字符是否符合QRC行格式
    if _QRC_LINE_RE.search(stripped[:500]):
        return "qrc"

    # 默认返回LRC格式
    return "lrc"


def _parse_lyrics_entries(raw: str) -> list[tuple[float, str]]:
    """解析歌词条目，根据检测的格式调用相应的解析函数。

    参数：
    raw (str): 原始歌词文本。

    返回值：
    list[tuple[float, str]]: 解析后的歌词条目列表，每个条目是一个包含时间戳和歌词文本的元组。
    """
    fmt = _detect_lyrics_format(raw)  # 检测歌词格式
    if fmt == "qrc":  # 检查是否为QRC格式
        return _parse_qrc_entries(raw)  # 如果是QRC格式，调用QRC解析函数
    return _parse_lrc_entries(raw)  # 否则，调用LRC解析函数


def _detect_lyrics_lang(filename: str) -> str:
    """功能：检测歌词文件的语言类型，基于文件名中的特定后缀或子串进行判断。
    参数：filename (str): 文件名字符串，可能为None。
    返回值：str: 检测到的语言类型，如'romaji'（罗马字）、'translation'（翻译）、'japanese'（日语）或'original'（原始）。
    """
    name = (filename or "").lower()  # 将文件名转换为小写，确保大小写不敏感；如果filename为None则使用空字符串
    if (
        name.endswith("_qmroma.qrc.txt") or "_qmroma." in name
    ):  # 检查文件名是否以"_qmroma.qrc.txt"结尾或包含"_qmroma."，以识别罗马字歌词
        return "romaji"
    if (
        name.endswith("_qmts.qrc.txt") or "_qmts." in name
    ):  # 检查文件名是否以"_qmts.qrc.txt"结尾或包含"_qmts."，以识别翻译歌词
        return "translation"
    if name.endswith("_qm.qrc.txt") or "_qm." in name:  # 检查文件名是否以"_qm.qrc.txt"结尾或包含"_qm."，以识别日语歌词
        return "japanese"
    return "original"  # 如果以上条件都不匹配，则默认返回原始歌词类型


def _extract_kana_content(raw: str) -> str:
    """从输入的字符串中提取假名内容。

    Args:
        raw (str): 原始字符串。

    Returns:
        str: 提取到的假名内容，如果没有匹配则返回空字符串。
    """
    # 使用预定义的正则表达式对象_KANA_RE在raw字符串中搜索假名内容
    m = _KANA_RE.search(raw)
    # 如果搜索到匹配，则返回匹配的第一个分组（即假名内容）
    if m:
        return m.group(1)
    # 如果没有匹配，则返回空字符串
    return ""


def _parse_kana_to_furigana_list(kana_content: str) -> list[str | None]:
    """
    将假名内容解析为振假名列表。

    参数:
        kana_content (str): 假名内容字符串。

    返回:
        list[str | None]: 振假名列表，其中None表示该位置没有振假名。
    """
    # 使用正则表达式清理输入字符串，移除_QRC_WORD_RE匹配的部分
    groups = list(_QRC_KANA_GROUP_RE.finditer(kana_content))
    # 初始化结果列表
    result: list[str | None] = []
    if groups:
        for match in groups:
            count = int(match.group(1))
            reading = _QRC_WORD_RE.sub("", match.group(0)[1:]) or None
            result.append(reading)
            result.extend([None] * (count - 1))
        return result
    cleaned = _QRC_WORD_RE.sub("", kana_content)
    i = 0
    # 遍历清理后的字符串
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "1":  # "1"作为标记，表示该位置没有振假名
            result.append(None)  # 追加None到结果
            i += 1  # 移动到下一个字符
        else:
            # 收集假名字符直到遇到"1"或字符串结束
            reading_chars: list[str] = []
            while i < len(cleaned) and cleaned[i] != "1":
                reading_chars.append(cleaned[i])
                i += 1
            # 将收集的字符连接成字符串并追加到结果
            result.append("".join(reading_chars))
    return result


def _parse_kana_timed(kana_content: str) -> list[tuple[str | None, int]]:
    """解析QRC格式的假名内容，提取文本和时间信息。

    该函数遍历输入的假名字符串，根据特定字符（如'1'和'('）识别时间节点，
    并将非节点字符收集为文本。最终返回一个列表，其中每个元素是一个元组，
    包含可选的文本和对应的开始时间（毫秒）。

    Args:
        kana_content: 包含QRC格式假名和时间标记的字符串。

    Returns:
        一个列表，列表中的每个元素是 (文本, 开始时间毫秒) 的元组。
        文本可能为 None，表示该时间节点前没有文本。
    """
    # 初始化结果列表，用于存储解析出的(文本, 时间)元组
    result: list[tuple[str | None, int]] = []
    i = 0
    n = len(kana_content)
    # 主循环，遍历整个输入字符串
    while i < n:
        ch = kana_content[i]
        # 情况1：遇到字符'1'，这通常表示一个换行或段落开始标记
        if ch == "1":
            start_ms = 0
            j = i + 1
            # 检查紧接着'1'后面是否有'('，可能包含时间信息
            if j < n and kana_content[j] == "(":
                # 使用预定义的正则表达式匹配时间模式
                m = _QRC_WORD_RE.match(kana_content, j)
                if m:
                    # 成功匹配，提取捕获组1中的毫秒时间
                    start_ms = int(m.group(1))
                    # 将索引j移动到匹配结束位置
                    j = m.end()
            # 将节点信息（文本为None）添加到结果
            result.append((None, start_ms))
            # 更新主索引i，跳过已处理的部分
            i = j
        # 情况2：单独遇到'('字符（前面没有文本或'1'标记）
        elif ch == "(":
            # 尝试匹配时间模式
            m = _QRC_WORD_RE.match(kana_content, i)
            if m:
                # 匹配成功，则移动索引到匹配结束，跳过这个时间节点
                i = m.end()
            else:
                # 匹配失败（格式不符），则仅跳过这个'('字符
                i += 1
        # 情况3：普通文本字符
        else:
            # 初始化一个列表，用于收集当前文本片段的所有字符
            reading_chars: list[str] = []
            start_ms = 0
            # 循环收集字符，直到遇到下一个节点标记（'1'或'('）或字符串结束
            while i < n and kana_content[i] not in ("1", "("):
                reading_chars.append(kana_content[i])
                i += 1
            # 将收集到的字符列表合并成一个字符串
            reading = "".join(reading_chars)
            # 检查当前索引位置是否是一个时间节点'('（即文本后面跟着时间）
            if i < n and kana_content[i] == "(":
                # 尝试匹配时间模式
                m = _QRC_WORD_RE.match(kana_content, i)
                if m:
                    # 匹配成功，提取开始时间毫秒
                    start_ms = int(m.group(1))
                    # 移动索引到匹配结束
                    i = m.end()
            # 将解析结果添加到列表，如果收集到的文本为空则记为None
            result.append((reading if reading else None, start_ms))
    # 返回最终解析结果
    return result


def _assign_furigana_to_entries(entries: list[LyricEntry], kana_content: str) -> None:
    """将注音内容分配到歌词条目中。

    解析注音字符串并分配给对应的歌词条目，为每个字符添加注音标注。

    参数:
        entries (list[LyricEntry]): 歌词条目列表，包含原始文本和需要添加的注音信息
        kana_content (str): 包含注音信息的字符串，格式为特定解析器能识别的格式

    返回:
        None: 该函数直接修改传入的entries列表，为每个条目添加注音信息
    """
    # 如果注音内容为空，直接返回不做处理
    if not kana_content:
        return

    # 解析注音字符串为注音列表
    furigana_list = _parse_kana_to_furigana_list(kana_content)

    # 如果解析结果为空，直接返回
    if not furigana_list:
        return

    # 当前处理到的注音索引
    kana_idx = 0

    # 遍历每个歌词条目
    for entry in entries:
        # 跳过原始文本为空的条目
        if not entry.original:
            continue

        # 计算当前条目中非空格字符的数量
        base_indexes = [index for index, char in enumerate(entry.original) if _QRC_FURIGANA_BASE_RE.fullmatch(char)]
        char_count = len(base_indexes)

        # 检查剩余注音数量是否足够分配给当前条目
        if kana_idx + char_count > len(furigana_list):
            break

        # 当前字符在原始文本中的索引

        # 初始化当前条目的注音列表
        entry.furigana = []

        # 遍历当前条目的每个字符
        for char_idx in base_indexes:
            # 跳过空格字符（全角和半角空格）

            # 检查注音索引是否超出范围
            if kana_idx >= len(furigana_list):
                break

            # 获取当前注音
            furi = furigana_list[kana_idx]

            # 如果当前注音不为空，则创建注音标注并添加到条目中
            if furi is not None:
                entry.furigana.append(FuriganaAnnotation(char_index=char_idx, text=furi))

            # 更新注音索引和字符索引
            kana_idx += 1


def build_structured_lyrics(
    main_raw: str,
    main_filename: str = "",
    extra_files: list[tuple[str, str]] | None = None,
) -> list[LyricEntry]:
    """从原始歌词文本构建结构化的歌词条目列表，支持合并多个歌词文件。

    Args:
        main_raw (str): 主歌词文件的原始文本内容。
        main_filename (str, optional): 主歌词文件的文件名，用于辅助检测歌词语言。默认为空字符串。
        extra_files (list[tuple[str, str]] | None, optional): 额外的歌词文件列表，每个元素为 (原始文本, 文件名) 的元组。默认为None。

    Returns:
        list[LyricEntry]: 按时间戳排序并填充了内容的歌词条目列表。
    """
    entries_list: list[LyricEntry] = []  # 用于存储最终所有歌词条目的列表
    kana_content = ""  # 用于存储提取的假名（振假名）内容
    _MERGE_TOLERANCE = 0.05  # 合并容差，用于判断两个时间戳是否足够接近以视为同一个歌词条目（单位：秒）

    def _find_or_create(ts: float) -> LyricEntry:
        """根据时间戳查找现有条目，若未找到则创建一个新条目。

        Args:
            ts (float): 歌词的时间戳。

        Returns:
            LyricEntry: 找到的或新创建的歌词条目。
        """
        # 遍历现有条目，检查是否有时间戳足够接近的条目
        for e in entries_list:
            if abs(e.timestamp - ts) < _MERGE_TOLERANCE:  # 使用容差进行比较
                return e
        # 未找到匹配条目，则创建新条目并加入列表
        e = LyricEntry(timestamp=ts)
        entries_list.append(e)
        return e

    def _extract_kana(raw: str) -> None:
        """从原始歌词文本中提取假名（振假名）内容，并更新到外部变量 kana_content。

        Args:
            raw (str): 原始歌词文本。
        """
        nonlocal kana_content  # 声明使用外部函数的 kana_content 变量
        kana = _extract_kana_content(raw)  # 调用外部函数提取假名内容
        # 如果成功提取到假名，并且新提取的内容比已存储的更长，则更新
        if kana and len(kana) > len(kana_content):
            kana_content = kana

    def _merge_lrc(raw: str, lang: str) -> None:
        """合并 LRC 格式歌词。

        Args:
            raw (str): LRC 格式的原始歌词文本。
            lang (str): 歌词的语言类型（如 "translation", "romaji" 等）。
        """
        # 遍历解析出的时间戳和歌词文本对
        for sec, text in _parse_lrc_entries(raw):
            e = _find_or_create(sec)  # 查找或创建对应时间戳的条目
            # 根据语言类型，将文本填充到条目的不同字段
            if lang == "translation":
                e.translation = text
            elif lang == "romaji":
                e.romaji = text
            else:  # 默认情况或其他语言（如原语言）
                e.original = text

    def _merge_qrc(raw: str, lang: str) -> None:
        """合并 QRC 格式歌词（QRC 格式包含逐词时间信息）。

        Args:
            raw (str): QRC 格式的原始歌词文本。
            lang (str): 歌词的语言类型。
        """
        # 遍历解析出的时间戳、歌词文本和单词时间信息
        for sec, text, words in _parse_qrc_structured(raw):
            e = _find_or_create(sec)  # 查找或创建对应时间戳的条目
            # 根据语言类型，将文本和单词时间信息填充到条目的对应字段
            if lang == "romaji":
                e.romaji = text
                e.romaji_words = words  # 逐词罗马音时间信息
            elif lang == "japanese":
                e.original = text
                e.original_words = words  # 逐词原文时间信息
            else:  # 默认情况
                e.original = text
                e.original_words = words

    def _merge(raw: str, filename: str) -> None:
        """合并单个歌词文件的核心逻辑。

        Args:
            raw (str): 歌词文件的原始文本内容。
            filename (str): 歌词文件的文件名，用于辅助检测语言和格式。
        """
        lang = _detect_lyrics_lang(filename)  # 检测歌词语言类型
        fmt = _detect_lyrics_format(raw)  # 检测歌词格式（LRC 或 QRC 等）
        _extract_kana(raw)  # 尝试提取假名内容
        # 根据检测到的格式，调用相应的合并函数
        if fmt == "qrc":
            _merge_qrc(raw, lang)
        else:  # 默认处理为 LRC 格式
            _merge_lrc(raw, lang)

    # 处理主歌词文件
    _merge(main_raw, main_filename)

    # 如果存在额外歌词文件，则逐一处理
    if extra_files:
        for raw, filename in extra_files:
            _merge(raw, filename)

    # 将所有条目按时间戳排序
    entries_list.sort(key=lambda e: e.timestamp)

    # 如果提取到了假名内容，则为所有条目分配振假名
    if kana_content:
        _assign_furigana_to_entries(entries_list, kana_content)
    return entries_list

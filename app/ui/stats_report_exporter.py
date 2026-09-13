"""年度报告导出器。

将 ReportData 渲染为打开即看的独立文件：
- HTML：自包含单文件（内联样式、无外部依赖），配色与主题保持一致（青柠绿强调色）。
- TXT：纯文本摘要，任何编辑器/记事本打开即可阅读。
- PNG：由报告窗口 QWidget.grab() 截图（见 stats_report_dialog）。

本模块零 Qt 依赖；theme.py 只含字符串常量，可安全导入。
"""

from __future__ import annotations

import html
from pathlib import Path

from app.services.stats_report_builder import ReportData
from app.ui.theme import ACCENT

# 导出 HTML 使用统一的深色调（与主题深色版一致），不随应用明暗切换，
# 保证导出物在任何环境打开都呈现一致观感。
_HTML_BG = "#161616"
_HTML_BG_ELEV = "#1f1f1f"
_HTML_BORDER = "#3b3b3b"
_HTML_TEXT = "#f1f1f1"
_HTML_DIM = "#9a9a9a"
_HTML_ACCENT = ACCENT
_HTML_UP = "#8fd977"
_HTML_DOWN = "#ef6b6b"


def _esc(text: str) -> str:
    """HTML 转义。"""
    return html.escape(str(text), quote=True)


def _fmt_hours(seconds: float) -> str:
    """秒数 → 小时字符串。"""
    return f"{seconds / 3600.0:.1f}"


def _fmt_count(value: int) -> str:
    """整数千分位格式化。"""
    return f"{int(value):,}"


def export_report_html(report: ReportData, secondary: ReportData | None = None) -> str:
    """渲染报告为自包含 HTML 字符串。

    Args:
        report: 主报告数据
        secondary: 副报告数据（有值时展示对比列）

    Returns:
        str: 完整 HTML 文档字符串
    """
    has_cmp = secondary is not None
    second_kpis = {kpi["key"]: kpi for kpi in secondary.kpis} if has_cmp else {}

    # ---- KPI 卡 ----
    kpi_cards: list[str] = []
    for kpi in report.kpis:
        delta_html = ""
        if has_cmp and kpi.get("delta_pct") is not None:
            cls = "up" if kpi["delta_pct"] >= 0 else "down"
            base = second_kpis.get(kpi["key"], {})
            delta_html = f'<div class="delta {cls}">{_esc(kpi["delta_text"])}（{_esc(secondary.title)}：{_esc(base.get("display", ""))}）</div>'
        kpi_cards.append(
            f'<div class="card kpi"><div class="label">{_esc(kpi["label"])}</div>'
            f'<div class="value">{_esc(kpi["display"])}</div>{delta_html}</div>'
        )

    # ---- 月度柱状图（纯 CSS）----
    max_sec = max(report.monthly_seconds or [0.0]) or 1.0
    bars: list[str] = []
    for idx in range(12):
        sec = report.monthly_seconds[idx] if idx < len(report.monthly_seconds) else 0.0
        play = report.monthly_play[idx] if idx < len(report.monthly_play) else 0
        height = round(sec / max_sec * 100, 1)
        tip = f"{idx + 1}月 · {_fmt_hours(sec)} 小时 · {play} 次"
        bars.append(
            f'<div class="mcol"><div class="mbar" style="height:{height}%" data-tip="{_esc(tip)}"></div><span>{idx + 1}</span></div>'
        )

    # ---- Top10 ----
    top_rows: list[str] = []
    for idx, row in enumerate(report.top_tracks):
        rank_cls = " gold" if idx < 3 else ""
        delta_cell = ""
        if has_cmp:
            d = row.get("delta")
            if d == "new":
                delta_cell = '<span class="tdelta new">NEW</span>'
            elif d == "up":
                delta_cell = f'<span class="tdelta up">▲{row["delta_rank"]}</span>'
            elif d == "down":
                delta_cell = f'<span class="tdelta down">▼{-row["delta_rank"]}</span>'
        top_rows.append(
            f'<div class="trow"><div class="rank{rank_cls}">{idx + 1}</div>'
            f'<div class="tinfo"><div class="t">{_esc(row["title"])}</div><div class="a">{_esc(row["artist"])}</div></div>'
            f'{delta_cell}<div class="tc"><div class="n">{row["count"]}</div><div class="u">次播放</div></div></div>'
        )

    # ---- 歌手/专辑 ----
    artist_rows = "".join(
        f'<div class="arow"><span>{idx + 1}. {_esc(row["name"])}</span><span class="ac">{_fmt_count(row["count"])} 次</span></div>'
        for idx, row in enumerate(report.top_artists)
    )
    album_rows = "".join(
        f'<div class="arow"><span>{idx + 1}. {_esc(row["name"])}</span><span class="ac">{_fmt_count(row["count"])} 次</span></div>'
        for idx, row in enumerate(report.top_albums)
    )

    # ---- 趣味发现 ----
    fact_cards = "".join(
        f'<div class="card fun"><div class="emoji">{_esc(fact["emoji"])}</div>'
        f'<div class="fv">{_esc(fact["title"])}</div><div class="fd">{_esc(fact["text"])}</div></div>'
        for fact in report.fun_facts
    )

    compare_banner = ""
    if has_cmp:
        compare_banner = (
            f'<div class="banner">⚖️ 正在与 <b>{_esc(secondary.title)}</b> 对比 — '
            f'<span class="up">▲</span>/<span class="down">▼</span> 为相对变化</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{_esc(report.title)} — MusePlayer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: {_HTML_BG}; color: {_HTML_TEXT};
         font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
         font-size: 14px; line-height: 1.6; padding: 32px; max-width: 1024px; margin: 0 auto; }}
  h1 {{ font-size: 26px; letter-spacing: 1px; }}
  .sub {{ color: {_HTML_DIM}; font-size: 13px; margin-bottom: 18px; }}
  .card {{ background: {_HTML_BG_ELEV}; border: 1px solid {_HTML_BORDER}; border-radius: 12px; padding: 16px 20px; }}
  .grid4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 16px; }}
  .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 16px 0; }}
  .grid2 {{ display: grid; grid-template-columns: 1.3fr 1fr; gap: 14px; margin-bottom: 16px; }}
  .kpi .label {{ color: {_HTML_DIM}; font-size: 12px; }}
  .kpi .value {{ font-size: 26px; font-weight: 700; margin: 2px 0; }}
  .delta {{ font-size: 12px; }}
  .up {{ color: {_HTML_UP}; }}
  .down {{ color: {_HTML_DOWN}; }}
  .banner {{ background: rgba(167,192,128,0.14); border: 1px solid rgba(167,192,128,0.4);
            border-radius: 10px; padding: 8px 16px; font-size: 13px; margin-bottom: 16px; }}
  .banner b {{ color: {_HTML_ACCENT}; }}
  h2 {{ font-size: 15px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
  h2::before {{ content: ""; width: 4px; height: 15px; background: {_HTML_ACCENT}; border-radius: 2px; }}
  .chart {{ display: flex; align-items: flex-end; gap: 6px; height: 140px; margin-top: 12px; }}
  .mcol {{ flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 6px; height: 100%; }}
  .mbar {{ width: 70%; background: linear-gradient(180deg, {_HTML_ACCENT}, rgba(167,192,128,0.35));
          border-radius: 5px 5px 2px 2px; min-height: 2px; position: relative; }}
  .mbar:hover::after {{ content: attr(data-tip); position: absolute; bottom: calc(100% + 6px); left: 50%;
    transform: translateX(-50%); background: #000; color: #fff; padding: 3px 8px; border-radius: 6px; font-size: 11px; white-space: nowrap; z-index: 5; }}
  .mcol span {{ font-size: 11px; color: {_HTML_DIM}; }}
  .trow {{ display: flex; align-items: center; gap: 12px; padding: 8px 4px; border-bottom: 1px solid {_HTML_BORDER}; }}
  .trow:last-child {{ border-bottom: none; }}
  .rank {{ width: 24px; height: 24px; border-radius: 50%; background: {_HTML_BG}; color: {_HTML_DIM};
          display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }}
  .rank.gold {{ background: #e0af68; color: #1a1508; }}
  .tinfo {{ flex: 1; min-width: 0; }}
  .t {{ font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .a {{ font-size: 11px; color: {_HTML_DIM}; }}
  .tdelta {{ font-size: 11px; font-weight: 700; flex-shrink: 0; }}
  .tdelta.new {{ color: #e0af68; }}
  .tc {{ text-align: right; flex-shrink: 0; }}
  .tc .n {{ font-weight: 700; }}
  .tc .u {{ font-size: 10px; color: {_HTML_DIM}; }}
  .arow {{ display: flex; justify-content: space-between; padding: 7px 4px; border-bottom: 1px solid {_HTML_BORDER}; font-size: 13px; }}
  .arow:last-child {{ border-bottom: none; }}
  .arow .ac {{ color: {_HTML_DIM}; font-size: 12px; }}
  .fun .emoji {{ font-size: 22px; }}
  .fun .fv {{ font-size: 18px; font-weight: 700; margin: 3px 0; }}
  .fun .fd {{ font-size: 12px; color: {_HTML_DIM}; }}
  .footer {{ color: {_HTML_DIM}; font-size: 11px; margin-top: 20px; text-align: center; }}
  @media (max-width: 860px) {{ .grid4, .grid3 {{ grid-template-columns: repeat(2, 1fr); }} .grid2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>{_esc(report.title)}</h1>
<div class="sub">{_esc(report.subtitle)} · 主数据：{_esc(report.source_label)} · 由 MusePlayer 导出</div>
{compare_banner}
<div class="grid4">{"".join(kpi_cards)}</div>
<div class="card"><h2>月度听歌节奏</h2><div class="chart">{"".join(bars)}</div></div>
<div class="grid2">
  <div class="card"><h2>最常听 · Top 10</h2>{"".join(top_rows) or '<div class="sub">暂无数据</div>'}</div>
  <div style="display:flex; flex-direction:column; gap:14px;">
    <div class="card"><h2>最常听的歌手</h2>{artist_rows or '<div class="sub">暂无数据</div>'}</div>
    <div class="card"><h2>最常听的专辑</h2>{album_rows or '<div class="sub">暂无数据</div>'}</div>
  </div>
</div>
<div class="grid3">{fact_cards or '<div class="card sub">暂无趣味数据</div>'}</div>
<div class="footer">MusePlayer 年度听歌报告 · 本文件为离线自包含 HTML，可直接双击查看</div>
</body>
</html>
"""


def export_report_text(report: ReportData, secondary: ReportData | None = None) -> str:
    """渲染报告为纯文本摘要（记事本打开即可阅读）。

    Args:
        report: 主报告数据
        secondary: 副报告数据（有值时附加对比行）

    Returns:
        str: 纯文本内容
    """
    lines: list[str] = []
    lines.append(report.title)
    lines.append(f"主数据：{report.source_label}（{report.subtitle}）")
    lines.append("由 MusePlayer 导出")
    lines.append("=" * 46)
    if secondary is not None:
        lines.append(f"【对比对象】{secondary.title}（{secondary.source_label}）")
        lines.append("-" * 46)
    lines.append("【核心指标】")
    second_kpis = {kpi["key"]: kpi for kpi in secondary.kpis} if secondary else {}
    for kpi in report.kpis:
        row = f"  {kpi['label']}：{kpi['display']}"
        if secondary and kpi.get("delta_pct") is not None:
            base = second_kpis.get(kpi["key"], {})
            row += f"（对比 {secondary.source_label}：{base.get('display', '')}，{kpi['delta_text']}）"
        lines.append(row)
    lines.append("")
    lines.append("【月度听歌节奏（小时）】")
    monthly = report.monthly_seconds or [0.0] * 12
    for idx, sec in enumerate(monthly):
        bar = "#" * max(0, int(sec / (max(monthly) or 1.0) * 30))
        lines.append(f"  {idx + 1:>2}月 {_fmt_hours(sec):>7}  {bar}")
    lines.append("")
    lines.append("【最常听 Top 10】")
    for idx, row in enumerate(report.top_tracks):
        mark = ""
        if secondary:
            d = row.get("delta")
            mark = (
                "  [NEW]"
                if d == "new"
                else (
                    f"  [▲{row['delta_rank']}]" if d == "up" else (f"  [▼{-row['delta_rank']}]" if d == "down" else "")
                )
            )
        lines.append(f"  {idx + 1:>2}. {row['title']} — {row['artist']}（{row['count']} 次）{mark}")
    if report.top_artists:
        lines.append("")
        lines.append("【最常听的歌手】")
        for idx, row in enumerate(report.top_artists):
            lines.append(f"  {idx + 1}. {row['name']}（{row['count']} 次）")
    if report.top_albums:
        lines.append("")
        lines.append("【最常听的专辑】")
        for idx, row in enumerate(report.top_albums):
            lines.append(f"  {idx + 1}. {row['name']}（{row['count']} 次）")
    if report.fun_facts:
        lines.append("")
        lines.append("【趣味发现】")
        for fact in report.fun_facts:
            lines.append(f"  {fact['emoji']} {fact['title']}：{fact['text']}")
    lines.append("")
    lines.append("=" * 46)
    return "\n".join(lines)


def write_report_export(dest: Path, report: ReportData, secondary: ReportData | None, fmt: str) -> Path:
    """将报告写入目标文件。

    Args:
        dest: 目标路径
        report: 主报告数据
        secondary: 副报告数据
        fmt: "html" / "txt" / "png"（png 由 UI 层截图处理，本函数不支持）

    Returns:
        Path: 实际写入的路径

    Raises:
        ValueError: 未知格式
    """
    fmt = fmt.lower().strip(".")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "html":
        dest = dest.with_suffix(".html")
        dest.write_text(export_report_html(report, secondary), encoding="utf-8")
    elif fmt == "txt":
        dest = dest.with_suffix(".txt")
        dest.write_text(export_report_text(report, secondary), encoding="utf-8")
    else:
        raise ValueError(f"不支持的导出格式：{fmt}")
    return dest

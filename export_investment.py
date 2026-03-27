#!/usr/bin/env python3
"""
投资中枢静态导出脚本
功能：读取Markdown报告 → 渲染HTML → 生成静态页面 → git推送

【重要原则】
- 本脚本只读取 Markdown 源文件，绝不修改它们
- 历史报告（持仓体检报告_YYYYMMDD.md）是只读快照，不可回溯修改
- 当日交易应记录到 memory/ 目录，或在次日生成新报告时纳入

用法：python3 export_investment.py
"""

import os
import re
import markdown
import subprocess
from pathlib import Path
from datetime import datetime

# 配置
LIGHTSABER_DIR = Path("/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber")
DOCS_DIR = LIGHTSABER_DIR / "docs"
ETF_DASHBOARD_DIR = Path("/Users/jediyang/ClaudeCode/Project-Makemoney/etf-dashboard")

MD_EXTENSIONS = ["tables", "fenced_code", "nl2br", "sane_lists"]


def get_reports() -> list[dict]:
    """扫描docs目录，找出所有体检报告，按日期倒序"""
    reports = []
    pattern = re.compile(r"持仓体检报告_(\d{8})\.md$")
    for f in DOCS_DIR.glob("持仓体检报告_*.md"):
        m = pattern.match(f.name)
        if m:
            date_str = m.group(1)
            try:
                date = datetime.strptime(date_str, "%Y%m%d")
                reports.append({
                    "filename": f.name,
                    "date_str": date_str,
                    "date_display": date.strftime("%Y年%m月%d日"),
                    "path": f,
                })
            except ValueError:
                continue
    return sorted(reports, key=lambda x: x["date_str"], reverse=True)


def get_html_reports() -> list[dict]:
    """扫描已生成的HTML报告，按时间倒序"""
    reports = []
    # 匹配 investment_YYYYMMDD_HHMMSS.html 格式
    pattern = re.compile(r"investment_(\d{8})_(\d{6})\.html$")
    for f in ETF_DASHBOARD_DIR.glob("investment_*.html"):
        if f.name == "investment.html":
            continue
        m = pattern.match(f.name)
        if m:
            date_str = m.group(1)
            time_str = m.group(2)
            try:
                dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
                reports.append({
                    "filename": f.name,
                    "date_str": date_str,
                    "time_str": time_str,
                    "datetime": dt,
                    "date_display": dt.strftime("%Y年%m月%d日 %H:%M"),
                    "path": f,
                })
            except ValueError:
                continue
    # 按时间倒序排列
    return sorted(reports, key=lambda x: x["datetime"], reverse=True)


def render_md(path: Path) -> str:
    """读取md文件并渲染为HTML"""
    if not path.exists():
        return f"<p>文件不存在: {path}</p>"
    text = path.read_text(encoding="utf-8")
    return markdown.markdown(text, extensions=MD_EXTENSIONS)


def parse_report_sections(text: str) -> dict:
    """解析报告，提取关键部分"""
    sections = {
        "title": "",
        "meta": {},
        "focus": "",
        "actions": "",
        "assets": "",
        "dimensions": {},
        "footer": ""
    }

    # 提取标题
    title_match = re.search(r'^# (.+)$', text, re.MULTILINE)
    if title_match:
        sections["title"] = title_match.group(1)

    # 提取元信息
    date_match = re.search(r'\*\*日期\*\*[:：]\s*(\d{4}-\d{2}-\d{2})', text)
    if date_match:
        sections["meta"]["date"] = date_match.group(1)

    version_match = re.search(r'\*\*框架版本\*\*[:：]\s*(.+?)(?:\n|$)', text)
    if version_match:
        sections["meta"]["version"] = version_match.group(1)

    source_match = re.search(r'\*\*数据来源\*\*[:：]\s*(.+?)(?:\n|$)', text)
    if source_match:
        sections["meta"]["source"] = source_match.group(1)

    # 提取本期重点关注
    focus_match = re.search(r'## 本期重点关注\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if focus_match:
        sections["focus"] = focus_match.group(1).strip()

    # 提取本期行动建议
    actions_match = re.search(r'## 本期行动建议\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if actions_match:
        sections["actions"] = actions_match.group(1).strip()

    # 提取总资产计算
    assets_match = re.search(r'## 总资产计算\s*\n(.*?)(?=\n## ▌|\n## 本期|\Z)', text, re.DOTALL)
    if assets_match:
        sections["assets"] = assets_match.group(1).strip()

    # 提取五维维度（在总资产和行动建议之后）
    dim_pattern = r'## ▌ (维度[一二三四五][^\n]*)\n(.*?)(?=\n## ▌ |\n## [^▌]|\n---|\Z)'
    for match in re.finditer(dim_pattern, text, re.DOTALL):
        dim_title = match.group(1).strip()
        dim_content = match.group(2).strip()
        sections["dimensions"][dim_title] = dim_content

    # 提取数据说明
    footer_match = re.search(r'## 数据说明\s*\n(.*?)(?=\n---|\Z)', text, re.DOTALL)
    if footer_match:
        sections["footer"] = footer_match.group(1).strip()

    return sections


def extract_simple_tables(text: str) -> list:
    """从文本中提取表格，返回(标题, 表格文本)列表"""
    tables = []

    # 匹配 markdown 表格
    table_pattern = r'((?:^[^\n]*\|[^\n]*\n)+)'
    lines = text.split('\n')

    in_table = False
    table_lines = []
    table_title = ""

    for i, line in enumerate(lines):
        if '|' in line and not line.strip().startswith('>'):
            if not in_table:
                # 表格开始，尝试找标题（前面一行）
                in_table = True
                table_lines = [line]
                if i > 0 and lines[i-1].strip() and not '|' in lines[i-1]:
                    table_title = lines[i-1].strip()
                else:
                    table_title = ""
            else:
                table_lines.append(line)
        else:
            if in_table:
                # 表格结束
                in_table = False
                if table_lines and len(table_lines) >= 2:
                    tables.append((table_title, '\n'.join(table_lines)))
                table_lines = []
                table_title = ""

    if in_table and table_lines and len(table_lines) >= 2:
        tables.append((table_title, '\n'.join(table_lines)))

    return tables


def generate_nav_html(report: dict, html_reports: list, current_filename: str) -> str:
    """生成侧边栏导航HTML"""
    # 生成历史报告链接
    history_links = ""
    for r in html_reports:
        active_class = "active" if r["filename"] == current_filename else ""
        history_links += f'<a href="{r["filename"]}" class="nav-link {active_class}">{r["date_display"]}</a>'

    # 本报告导航链接 - 重要内容放前面
    toc_links = """
<a href="#focus" class="nav-link" style="border-left-color:#ff5252;color:#ff5252;">本期重点关注</a>
<a href="#actions" class="nav-link" style="border-left-color:#00c853;color:#00c853;">本期行动建议</a>
<a href="#assets" class="nav-link">总资产计算</a>
<a href="#dim1" class="nav-link highlight">维度一：集中度风险</a>
<a href="#dim2" class="nav-link highlight">维度二：波段仓状态</a>
<a href="#dim3" class="nav-link highlight">维度三：投资逻辑验证</a>
<a href="#dim4" class="nav-link highlight">维度四：归因分析</a>
<a href="#dim5" class="nav-link highlight">维度五：组合健康度预判</a>
<a href="#footer" class="nav-link">数据说明</a>
"""

    return f"""
<div class="sidebar">
    <div class="sidebar-card">
        <div class="sidebar-title">历史报告</div>
        {history_links}
    </div>
    <div class="sidebar-card">
        <div class="sidebar-title">本报告导航</div>
        {toc_links}
    </div>
</div>
"""


def generate_focus_section(text: str) -> str:
    """生成本期重点关注HTML"""
    if not text:
        return ""

    # 解析列表项
    lines = text.strip().split('\n')
    items = []
    for line in lines:
        line = line.strip()
        if line.startswith('1.') or line.startswith('2.') or line.startswith('3.'):
            # 提取序号和内容
            content = re.sub(r'^\d+\.\s*', '', line)
            # 处理 **加粗**
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            items.append(content)
        elif line.startswith('- ') or line.startswith('* '):
            content = line[2:]
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            items.append(content)

    if not items:
        # 如果没有解析到列表，直接渲染markdown
        html = markdown.markdown(text, extensions=MD_EXTENSIONS)
        return f'<h2 id="focus">本期重点关注</h2>\n{html}'

    html_items = '\n'.join([f'<li>{item}</li>' for item in items])
    return f"""
<h2 id="focus">本期重点关注</h2>
<ol>
{html_items}
</ol>
"""


def generate_actions_section(text: str) -> str:
    """生成本期行动建议HTML"""
    if not text:
        return ""

    # 先渲染markdown
    html = markdown.markdown(text, extensions=MD_EXTENSIONS)

    # 处理P0/P1/P2标签样式
    html = re.sub(r'>\s*P0\s*<', '><span class="tag tag-p0">P0</span><', html)
    html = re.sub(r'>\s*P1\s*<', '><span class="tag tag-p1">P1</span><', html)
    html = re.sub(r'>\s*P2\s*<', '><span class="tag tag-p2">P2</span><', html)

    return f'<h2 id="actions">本期行动建议</h2>\n{html}'


def generate_assets_section(text: str) -> str:
    """生成总资产计算HTML，将详细表格折叠"""
    if not text:
        return ""

    # 提取总资产汇总信息
    total_match = re.search(r'\*\*总计\*\*[:：]?\s*(.+?)(?:\n|$)', text)
    total_text = total_match.group(1) if total_match else ""

    # 提取市场分布表
    market_pattern = r'\| 市场 \| 折合CNY \| 占比 \|\n\|[-\| ]+\|\n((?:\|[^\n]+\|\n)+)'
    market_match = re.search(market_pattern, text)
    market_html = ""
    if market_match:
        market_table = "| 市场 | 金额 | 占比 |\n|------|------|------|\n" + market_match.group(1)
        market_html = markdown.markdown(market_table, extensions=MD_EXTENSIONS)

    # 提取详细价格表（折叠）
    price_tables = []

    # 港股价格表
    hk_pattern = r'### 港股.*?\n\n\| 标的 \| 股数 \| 现价[^\n]+ \| 市值[^\n]+ \|\n\|[-\| ]+\|\n((?:\|[^\n]+\|\n)+)'
    hk_match = re.search(hk_pattern, text, re.DOTALL)
    if hk_match:
        hk_table = "| 标的 | 股数 | 现价 | 市值 |\n|------|------|------|------|\n" + hk_match.group(1)
        price_tables.append(("港股持仓明细", hk_table))

    # 美股价格表
    us_pattern = r'### 美股.*?\n\n\| 标的 \| 股数 \| 现价[^\n]+ \| 市值[^\n]+ \|\n\|[-\| ]+\|\n((?:\|[^\n]+\|\n)+)'
    us_match = re.search(us_pattern, text, re.DOTALL)
    if us_match:
        us_table = "| 标的 | 股数 | 现价 | 市值 |\n|------|------|------|------|\n" + us_match.group(1)
        price_tables.append(("美股持仓明细", us_table))

    # A股价格表
    a_pattern = r'### A股.*?\n\n\| 标的 \| 股数 \| 现价[^\n]+ \| 市值[^\n]+ \|\n\|[-\| ]+\|\n((?:\|[^\n]+\|\n)+)'
    a_match = re.search(a_pattern, text, re.DOTALL)
    if a_match:
        a_table = "| 标的 | 股数 | 现价 | 市值 |\n|------|------|------|------|\n" + a_match.group(1)
        price_tables.append(("A股持仓明细", a_table))

    # 价格基准表（富途实时）
    price_pattern = r'### 价格基准.*?\n\n\| 代码 \| 名称 \| 价格 \| 货币 \|\n\|[-\| ]+\|\n((?:\|[^\n]+\|\n)+)'
    price_match = re.search(price_pattern, text, re.DOTALL)
    if price_match:
        p_table = "| 代码 | 名称 | 价格 | 货币 |\n|------|------|------|------|\n" + price_match.group(1)
        price_tables.append(("详细价格表（港/美股实时）", p_table))

    # 生成折叠详情
    details_html = ""
    for title, table_md in price_tables:
        table_html = markdown.markdown(table_md, extensions=MD_EXTENSIONS)
        details_html += f"""
<details>
<summary>{title}</summary>
{table_html}
</details>
"""

    return f"""
<h2 id="assets">总资产计算</h2>

<p><strong>{total_text}</strong></p>

{market_html}

{details_html}
"""


def generate_dimension_section(dim_id: str, title: str, content: str) -> str:
    """生成单个维度HTML"""
    if not content:
        return ""

    # 渲染内容
    html = markdown.markdown(content, extensions=MD_EXTENSIONS)

    # 提取表格并折叠（如果表格行数较多）
    # 查找所有表格
    table_pattern = r'<table>.*?</table>'
    tables = re.findall(table_pattern, html, re.DOTALL)

    # 如果有多于2个表格，将非第一个的表格折叠
    if len(tables) > 2:
        # 简化处理：保留前两个表格，其余折叠
        # 实际实现需要更复杂的DOM操作，这里简化
        pass

    return f"""
<h2 id="{dim_id}">{title}</h2>
{html}
"""


def generate_report_html(report: dict, html_reports: list, current_filename: str) -> str:
    """生成单个报告页面HTML - 朴素长文风格"""

    # 读取原始内容并解析
    raw_text = report["path"].read_text(encoding="utf-8")
    sections = parse_report_sections(raw_text)

    # 生成导航
    nav_html = generate_nav_html(report, html_reports, current_filename)

    # 生成各部分内容
    focus_html = generate_focus_section(sections["focus"])
    actions_html = generate_actions_section(sections["actions"])
    assets_html = generate_assets_section(sections["assets"])

    # 生成维度部分
    dimensions_html = ""
    dim_order = [
        ("dim1", "维度一：集中度风险"),
        ("dim2", "维度二：波段仓状态"),
        ("dim3", "维度三：投资逻辑验证"),
        ("dim4", "维度四：归因分析"),
        ("dim5", "维度五：组合健康度预判"),
    ]

    for dim_id, dim_name in dim_order:
        for title, content in sections["dimensions"].items():
            if dim_name in title:
                dimensions_html += generate_dimension_section(dim_id, title, content)
                break

    # 生成数据说明
    footer_html = ""
    if sections["footer"]:
        footer_md = markdown.markdown(sections["footer"], extensions=MD_EXTENSIONS)
        footer_html = f'<h2 id="footer">数据说明</h2>\n{footer_md}'

    # 报告头部信息
    date_display = report["date_display"]
    date_str = sections["meta"].get("date", report["date_str"])
    version = sections["meta"].get("version", "五维雷达 v1.0")
    source = sections["meta"].get("source", "富途OpenD实时价格")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>投资中枢 · {date_display}体检报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #eceaf8;
            color: #131722;
            line-height: 1.7;
            font-size: 15px;
        }}

        .container {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 20px;
        }}

        /* Header Band - 紫色顶部导航 */
        .header-band {{
            background: #38346a;
            box-shadow: 0 4px 20px rgba(0,0,0,0.18);
            width: 100%;
            margin-bottom: 20px;
        }}

        .header-inner {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 28px;
        }}

        .header-nav {{
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .nav-brand {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 16px;
            font-weight: 600;
            color: #fff;
            text-decoration: none;
            letter-spacing: -0.3px;
        }}

        .nav-brand span {{
            color: #a5b4fc;
        }}

        .nav-links {{
            display: flex;
            gap: 4px;
        }}

        .nav-link-top {{
            padding: 5px 14px;
            border-radius: 20px;
            color: rgba(255,255,255,0.55);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
        }}

        .nav-link-top.active {{
            background: rgba(255,255,255,0.15);
            color: #fff;
        }}

        .header-title-area {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0 16px;
            border-top: 1px solid rgba(255,255,255,0.1);
            margin-top: 8px;
        }}

        .title-left {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .title-icon {{
            font-size: 20px;
        }}

        .title-text {{
            font-size: 20px;
            font-weight: 600;
            color: #fff;
        }}

        .title-date {{
            font-size: 14px;
            color: rgba(255,255,255,0.5);
            margin-left: 8px;
        }}

        .title-actions {{
            display: flex;
            gap: 6px;
        }}

        .btn-outline {{
            padding: 5px 12px;
            border-radius: 5px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.75);
            font-size: 12px;
            font-weight: 500;
            text-decoration: none;
        }}

        /* 主布局 */
        .main-layout {{
            display: flex;
            gap: 40px;
            align-items: flex-start;
        }}

        /* 左侧边栏 - sticky 固定 */
        .sidebar {{
            width: 200px;
            flex-shrink: 0;
            position: sticky;
            top: 20px;
            align-self: flex-start;
        }}

        .sidebar-card {{
            background: #fff;
            border-radius: 8px;
            border: 1px solid #dcdaf0;
            padding: 16px;
            margin-bottom: 16px;
        }}

        .sidebar-title {{
            font-size: 11px;
            color: #666;
            font-weight: 600;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .sidebar a {{
            display: block;
            padding: 6px 0;
            font-size: 14px;
            color: #495057;
            text-decoration: none;
            border-left: 3px solid transparent;
            padding-left: 12px;
            margin-left: -12px;
        }}

        .sidebar a:hover {{
            color: #2962ff;
        }}

        .sidebar a.active {{
            border-left-color: #38346a;
            color: #38346a;
            font-weight: 500;
        }}

        .sidebar .highlight {{
            border-left-color: #868e96;
        }}

        /* 内容区 - 白色卡片 */
        .content-wrapper {{
            flex: 1;
            background: #fff;
            border-radius: 8px;
            border: 1px solid #dcdaf0;
            padding: 32px 40px;
            max-width: 900px;
        }}

        /* 报告头部 */
        .report-header {{
            margin-bottom: 32px;
            padding-bottom: 24px;
            border-bottom: 1px solid #e0e3eb;
        }}

        .report-header h2 {{
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 16px;
            border: none;
            padding: 0;
        }}

        .report-meta {{
            font-size: 14px;
            color: #787b86;
            line-height: 1.8;
        }}

        /* 章节标题 - 左侧竖线区隔 */
        h2 {{
            font-size: 18px;
            font-weight: 600;
            margin: 32px 0 16px 0;
            padding-left: 12px;
            border-left: 4px solid #131722;
            line-height: 1.4;
        }}

        h3 {{
            font-size: 15px;
            font-weight: 600;
            margin: 24px 0 12px 0;
            color: #333;
        }}

        p {{
            margin-bottom: 12px;
        }}

        ul, ol {{
            margin: 12px 0;
            padding-left: 24px;
        }}

        li {{
            margin-bottom: 8px;
        }}

        /* 表格 - 极简 */
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            margin: 16px 0;
        }}

        th {{
            text-align: left;
            padding: 10px 8px;
            border-bottom: 1px solid #e0e3eb;
            font-weight: 500;
            color: #787b86;
            font-size: 13px;
        }}

        td {{
            padding: 10px 8px;
            border-bottom: 1px solid #f0f3fa;
        }}

        tr:last-child td {{
            border-bottom: 1px solid #e0e3eb;
        }}

        /* 折叠组件 */
        details {{
            margin: 16px 0;
        }}

        summary {{
            cursor: pointer;
            color: #2962ff;
            font-size: 14px;
            user-select: none;
            list-style: none;
        }}

        summary::-webkit-details-marker {{
            display: none;
        }}

        summary::before {{
            content: "▶";
            display: inline-block;
            margin-right: 6px;
            font-size: 10px;
            transition: transform 0.2s;
        }}

        details[open] summary::before {{
            transform: rotate(90deg);
        }}

        details[open] summary {{
            margin-bottom: 12px;
        }}

        /* 优先级标签 */
        .tag {{
            display: inline-block;
            padding: 2px 6px;
            background: #f5f6f7;
            border-radius: 3px;
            font-size: 12px;
            font-weight: 500;
            margin-right: 8px;
            color: #495057;
        }}

        /* 移动端适配 */
        @media (max-width: 768px) {{
            .container {{
                padding: 0 12px;
            }}

            .header-inner {{
                padding: 0 16px;
            }}

            .main-layout {{
                flex-direction: column;
                gap: 16px;
            }}

            .sidebar {{
                width: 100%;
                order: -1;
                position: static;
            }}

            .sidebar-card {{
                margin-bottom: 12px;
            }}

            .content-wrapper {{
                padding: 20px;
            }}

            .report-header h2 {{
                font-size: 22px;
            }}

            h2 {{
                font-size: 17px;
            }}

            table {{
                font-size: 13px;
            }}

            .header-title-area {{
                flex-direction: column;
                align-items: flex-start;
                gap: 12px;
            }}

            .title-actions {{
                width: 100%;
            }}
        }}
    </style>
</head>
<body>
    <!-- Header Band -->
    <div class="header-band">
        <div class="header-inner">
            <div class="header-nav">
                <a href="/" class="nav-brand">
                    <span>⚔️</span>
                    <span>光剑 Lightsaber</span>
                </a>
                <div class="nav-links">
                    <a href="/positions/" class="nav-link-top">持仓</a>
                    <a href="/investment/" class="nav-link-top active">投资中枢</a>
                </div>
            </div>
            <div class="header-title-area">
                <div class="title-left">
                    <span class="title-icon">📊</span>
                    <span class="title-text">体检报告</span>
                    <span class="title-date">{date_display}</span>
                </div>
                <div class="title-actions">
                    <a href="investment.html" class="btn-outline">最新报告</a>
                    <a href="index.html" class="btn-outline">← ETF轮动</a>
                </div>
            </div>
        </div>
    </div>

    <!-- 主体 -->
    <div class="container">
        <div class="main-layout">
            {nav_html}
            <div class="content-wrapper">
                <div class="report-header">
                    <h2>光剑系统 · 五维雷达体检报告</h2>
                    <div class="report-meta">
                        日期: {date_str}<br>
                        框架版本: {version}<br>
                        数据来源: {source}
                    </div>
                </div>

                {focus_html}

                {actions_html}

                {assets_html}

                {dimensions_html}

                {footer_html}

                <hr style="margin: 40px 0 20px;">
                <p style="font-size: 13px; color: #787b86;">
                    生成时间: {date_str} | 框架: {version}
                </p>
            </div>
        </div>
    </div>
</body>
</html>"""


def generate_index_html(report: dict, html_reports: list, latest_html_filename: str) -> str:
    """生成投资中枢首页（最新报告）"""
    return generate_report_html(report, html_reports, latest_html_filename)


def git_push():
    """执行git提交和推送"""
    try:
        os.chdir(ETF_DASHBOARD_DIR)

        # 检查是否有变更
        result = subprocess.run(['git', 'status', '--porcelain'],
                              capture_output=True, text=True)
        if not result.stdout.strip():
            print("⚠️  没有变更需要提交")
            return True

        # git add
        subprocess.run(['git', 'add', '.'], check=True)

        # git commit
        today = datetime.now().strftime('%Y-%m-%d')
        subprocess.run(['git', 'commit', '-m', f'Update: {today} investment reports'], check=True)

        # git push
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)

        print("✅ Git推送成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Git操作失败: {e}")
        return False


def main():
    print("=" * 60)
    print("投资中枢静态导出")
    print("=" * 60)

    # 扫描MD报告
    print("\n扫描体检报告...")
    md_reports = get_reports()
    print(f"发现 {len(md_reports)} 份MD报告")

    if not md_reports:
        print("❌ 没有找到体检报告")
        return False

    # 扫描已存在的HTML报告
    html_reports = get_html_reports()
    print(f"发现 {len(html_reports)} 份已生成的HTML报告")

    # 获取最新MD报告
    latest_md = md_reports[0]
    today = datetime.now()
    timestamp = today.strftime('%Y%m%d_%H%M%S')

    # 生成新的历史报告页面（带时间戳，永不覆盖）
    print(f"\n生成历史报告页面: {timestamp}...")
    history_filename = f"investment_{timestamp}.html"
    history_path = ETF_DASHBOARD_DIR / history_filename

    # 为最新报告生成HTML（添加到历史列表的开头）
    new_report_info = {
        "filename": history_filename,
        "date_str": today.strftime('%Y%m%d'),
        "time_str": today.strftime('%H%M%S'),
        "datetime": today,
        "date_display": today.strftime("%Y年%m月%d日 %H:%M"),
        "path": history_path,
    }

    # 生成最新历史报告HTML
    html = generate_report_html(latest_md, [new_report_info] + html_reports, history_filename)
    with open(history_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ {history_path}")

    # 更新首页（investment.html），指向最新生成的历史页面
    print("\n生成投资中枢首页...")
    index_html = generate_index_html(latest_md, [new_report_info] + html_reports, history_filename)
    index_path = ETF_DASHBOARD_DIR / "investment.html"
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(index_html)
    print(f"✅ {index_path}")

    # Git推送
    print("\n推送到GitHub...")
    if git_push():
        print("\n🎉 导出完成！")
        print(f"投资中枢: https://smiling-jedi.github.io/etf-dashboard-/investment.html")
        print(f"本次报告: https://smiling-jedi.github.io/etf-dashboard-/{history_filename}")
        return True
    else:
        print("\n⚠️  页面已生成，但推送失败")
        return False


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)

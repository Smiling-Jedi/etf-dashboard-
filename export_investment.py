#!/usr/bin/env python3
"""
投资中枢静态导出脚本
功能：读取Markdown报告 → 渲染HTML → 生成静态页面 → git推送
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
        "header": "",  # 日期、框架版本等
        "focus": "",   # 本期重点关注
        "actions": "", # 本期行动建议
        "radar_summary": {},  # 五维雷达摘要
        "dimensions": {},  # 五个维度详情
        "assets": "",  # 总资产计算
        "footer": ""   # 数据说明等
    }

    # 提取标题和头部信息
    title_match = re.search(r'^# (.+)$', text, re.MULTILINE)
    if title_match:
        sections["title"] = title_match.group(1)

    # 提取头部元信息（日期、框架版本等）
    header_pattern = r'\*\*日期\*\*.*?(?=\n\n|\n#{1,2} )'
    header_match = re.search(header_pattern, text, re.DOTALL)
    if header_match:
        sections["header"] = header_match.group(0)

    # 提取本期重点关注
    focus_match = re.search(r'## 本期重点关注\s*\n(.*?)(?=\n## |\n---|\Z)', text, re.DOTALL)
    if focus_match:
        sections["focus"] = focus_match.group(1).strip()

    # 提取本期行动建议
    actions_match = re.search(r'## 本期行动建议\s*\n(.*?)(?=\n## |\n---|\Z)', text, re.DOTALL)
    if actions_match:
        sections["actions"] = actions_match.group(1).strip()

    # 提取五维雷达各维度
    dimension_pattern = r'## ▌ (维度[一二三四五][^\n]*)\n(.*?)(?=\n## ▌ |\n## [^▌]|\n---|\Z)'
    for match in re.finditer(dimension_pattern, text, re.DOTALL):
        dim_title = match.group(1).strip()
        dim_content = match.group(2).strip()
        sections["dimensions"][dim_title] = dim_content

    # 提取总资产计算
    assets_match = re.search(r'## 总资产计算\s*\n(.*?)(?=\n## |\n---|\Z)', text, re.DOTALL)
    if assets_match:
        sections["assets"] = assets_match.group(1).strip()

    # 提取数据说明
    footer_match = re.search(r'## 数据说明\s*\n(.*?)(?=\n---|\Z)', text, re.DOTALL)
    if footer_match:
        sections["footer"] = footer_match.group(1).strip()

    return sections


def extract_radar_status(text: str) -> list:
    """从报告中提取五维雷达状态"""
    status = []

    # 维度一：集中度 - 查找 HHI 状态
    dim1_match = re.search(r'HHI.*?\|.*?\| ([🟢🟡🔴])', text)
    if dim1_match:
        status.append(("集中度风险", dim1_match.group(1), "HHI健康"))

    # 维度二：波段仓 - 查找是否有被套标的
    dim2_bear = len(re.findall(r'[🔴🟡].*?被套', text))
    if dim2_bear > 0:
        status.append(("波段仓状态", "🟡", f"{dim2_bear}只被套"))
    else:
        status.append(("波段仓状态", "🟢", "健康"))

    # 维度三：逻辑验证 - 统计失效数量
    dim3_fail = len(re.findall(r'❌|失效|⚠️', text.split("## ▌ 维度三")[1].split("## ▌ 维度四")[0] if "## ▌ 维度三" in text else ""))
    if dim3_fail > 0:
        status.append(("逻辑验证", "🔴", f"{dim3_fail}项需关注"))
    else:
        status.append(("逻辑验证", "🟢", "正常"))

    # 维度五：组合健康度
    dim5_match = re.search(r'组合预期收益约(\d+)%', text)
    if dim5_match:
        expected = int(dim5_match.group(1))
        if expected >= 25:
            status.append(("组合健康度", "🟢", f"预期{expected}%"))
        elif expected >= 20:
            status.append(("组合健康度", "🟡", f"预期{expected}%"))
        else:
            status.append(("组合健康度", "🔴", f"预期{expected}%"))

    return status


def generate_mobile_optimized_html(sections: dict, original_html: str, radar_status: list) -> str:
    """生成移动端优化的HTML"""

    # 渲染各部分
    focus_html = markdown.markdown(sections["focus"], extensions=MD_EXTENSIONS) if sections["focus"] else ""
    actions_html = markdown.markdown(sections["actions"], extensions=MD_EXTENSIONS) if sections["actions"] else ""

    # 生成五维雷达快速预览
    radar_overview = ""
    for name, icon, desc in radar_status:
        radar_overview += f'<div class="radar-item"><span class="radar-icon">{icon}</span><span class="radar-name">{name}</span><span class="radar-desc">{desc}</span></div>'

    # 生成维度折叠区域
    dimensions_accordion = ""
    dim_order = [
        ("维度一：集中度风险", "concentration"),
        ("维度二：波段仓状态", "momentum"),
        ("维度三：投资逻辑验证", "logic"),
        ("维度四：归因分析", "attribution"),
        ("维度五：组合健康度预判", "health")
    ]

    for dim_name, dim_id in dim_order:
        for title, content in sections["dimensions"].items():
            if dim_name in title:
                dim_html = markdown.markdown(content, extensions=MD_EXTENSIONS)
                dimensions_accordion += f'''
<details class="dim-details" id="{dim_id}">
<summary>{dim_name}</summary>
<div class="dim-content">{dim_html}</div>
</details>
'''
                break

    # 生成资产折叠区域
    assets_html = markdown.markdown(sections["assets"], extensions=MD_EXTENSIONS) if sections["assets"] else ""
    assets_section = f'''
<details class="dim-details" id="assets">
<summary>💰 总资产计算明细</summary>
<div class="dim-content">{assets_html}</div>
</details>
''' if assets_html else ""

    # 返回重组后的HTML内容部分
    return f'''
<!-- 移动端优化：核心信息置顶 -->
<div class="mobile-optimized">
    <!-- 本期重点关注 -->
    <div class="priority-section alert-section">
        <div class="section-header">
            <span class="section-icon">🔥</span>
            <h2>本期重点关注</h2>
        </div>
        <div class="section-body">{focus_html}</div>
    </div>

    <!-- 本期行动建议 -->
    <div class="priority-section action-section">
        <div class="section-header">
            <span class="section-icon">⚡</span>
            <h2>本期行动建议</h2>
        </div>
        <div class="section-body">{actions_html}</div>
    </div>

    <!-- 五维雷达总览 -->
    <div class="radar-overview">
        <div class="section-header">
            <span class="section-icon">📊</span>
            <h2>五维雷达总览</h2>
        </div>
        <div class="radar-grid">{radar_overview}</div>
        <div class="radar-nav-hint">👇 点击下方展开各维度详情</div>
    </div>
</div>

<!-- 详细内容（折叠） -->
<div class="details-section">
    {dimensions_accordion}
    {assets_section}
</div>

<!-- 原始完整报告 -->
<div class="original-report">
    <details class="dim-details original-details">
        <summary>📄 查看完整原始报告</summary>
        <div class="dim-content original-content">{original_html}</div>
    </details>
</div>
'''



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


def generate_report_html(report: dict, reports: list) -> str:
    """生成单个报告页面HTML - 移动端优化版"""

    # 读取原始内容
    raw_text = report["path"].read_text(encoding="utf-8")
    original_html = markdown.markdown(raw_text, extensions=MD_EXTENSIONS)

    # 解析报告各部分
    sections = parse_report_sections(raw_text)

    # 提取雷达状态
    radar_status = extract_radar_status(raw_text)

    # 生成移动端优化的内容
    optimized_content = generate_mobile_optimized_html(sections, original_html, radar_status)

    # 生成历史报告链接
    history_links = ""
    for r in reports:
        active_class = "active" if r["date_str"] == report["date_str"] else ""
        history_links += f'<a href="investment_{r["date_str"]}.html" class="archive-link {active_class}">{r["date_display"]}</a>\n'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>投资中枢 · {report["date_display"]}体检报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #f5f6f7;
            color: #131722;
            line-height: 1.6;
        }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}

        /* 头部 */
        .header {{
            background: #fff;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .header h1 {{
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .header-meta {{
            font-size: 13px;
            color: #787b86;
        }}
        .nav {{
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid #e0e3eb;
        }}
        .nav a {{
            display: inline-block;
            padding: 8px 16px;
            margin-right: 8px;
            background: #f0f7ff;
            color: #2962ff;
            text-decoration: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }}
        .nav a:hover {{ background: #e0efff; }}

        /* 主布局 */
        .main-layout {{
            display: flex;
            gap: 20px;
        }}
        .sidebar {{
            width: 200px;
            flex-shrink: 0;
        }}
        .content {{
            flex: 1;
            background: #fff;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}

        /* 侧边栏 */
        .sidebar-card {{
            background: #fff;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .sidebar-title {{
            font-size: 12px;
            font-weight: 600;
            color: #787b86;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .archive-link {{
            display: block;
            padding: 8px 10px;
            border-radius: 6px;
            font-size: 13px;
            color: #495057;
            text-decoration: none;
            margin-bottom: 4px;
        }}
        .archive-link:hover {{ background: #f5f6f7; }}
        .archive-link.active {{ background: #e9ecef; font-weight: 600; color: #000; }}

        /* 移动端优化样式 - 优先区块 */
        .priority-section {{
            margin-bottom: 20px;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 16px 20px;
            font-size: 16px;
            font-weight: 600;
        }}
        .section-icon {{ font-size: 20px; }}
        .section-header h2 {{
            font-size: 16px;
            font-weight: 600;
            margin: 0;
        }}
        .section-body {{
            padding: 20px;
            background: #fff;
        }}

        /* 重点关注 - 红色警示 */
        .alert-section .section-header {{
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a5a 100%);
            color: #fff;
        }}
        .alert-section .section-body {{
            background: #fff5f5;
            border: 2px solid #ffc9c9;
            border-top: none;
            border-radius: 0 0 12px 12px;
        }}

        /* 行动建议 - 蓝色强调 */
        .action-section .section-header {{
            background: linear-gradient(135deg, #4dabf7 0%, #339af0 100%);
            color: #fff;
        }}
        .action-section .section-body {{
            background: #f0f7ff;
            border: 2px solid #d0ebff;
            border-top: none;
            border-radius: 0 0 12px 12px;
        }}

        /* 五维雷达总览 */
        .radar-overview {{
            background: #fff;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .radar-overview .section-header {{
            padding: 0 0 16px 0;
            border-bottom: 1px solid #e0e3eb;
        }}
        .radar-overview .section-header h2 {{ color: #333; }}
        .radar-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            padding-top: 16px;
        }}
        .radar-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 12px 16px;
            background: #f8f9fa;
            border-radius: 8px;
            font-size: 14px;
        }}
        .radar-icon {{ font-size: 18px; }}
        .radar-name {{ font-weight: 500; color: #333; }}
        .radar-desc {{
            margin-left: auto;
            font-size: 12px;
            color: #787b86;
        }}

        /* 折叠详情 */
        .details-section {{ margin-bottom: 20px; }}
        .dim-details {{
            background: #fff;
            border-radius: 12px;
            margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            overflow: hidden;
        }}
        .dim-details summary {{
            padding: 16px 20px;
            font-size: 15px;
            font-weight: 600;
            color: #333;
            cursor: pointer;
            list-style: none;
        }}
        .dim-details summary::before {{
            content: "▶";
            font-size: 12px;
            color: #787b86;
            margin-right: 8px;
            display: inline-block;
            transition: transform 0.2s;
        }}
        .dim-details[open] summary::before {{
            transform: rotate(90deg);
        }}
        .dim-content {{
            padding: 0 20px 20px;
            border-top: 1px solid #f0f3fa;
        }}

        /* 报告内容样式 */
        .report-content h1 {{ font-size: 22px; margin-bottom: 16px; }}
        .report-content h2 {{ font-size: 18px; margin-top: 24px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #e0e3eb; }}
        .report-content h3 {{ font-size: 16px; margin-top: 20px; margin-bottom: 10px; }}
        .report-content p {{ margin-bottom: 12px; }}
        .report-content table {{
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
            font-size: 14px;
        }}
        .report-content th {{
            background: #f8f9fa;
            padding: 10px 12px;
            border: 1px solid #e0e3eb;
            text-align: left;
            font-weight: 600;
        }}
        .report-content td {{
            padding: 10px 12px;
            border: 1px solid #e0e3eb;
        }}
        .report-content tr:nth-child(even) {{ background: #fafafa; }}

        /* 移动端适配 */
        @media (max-width: 768px) {{
            .main-layout {{ flex-direction: column; }}
            .sidebar {{ width: 100%; }}
            .radar-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}
        @media (max-width: 480px) {{
            .radar-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 投资中枢 · 体检报告</h1>
            <div class="header-meta">报告日期: {report["date_display"]} | 数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div class="nav">
                <a href="investment.html">最新报告</a>
                <a href="index.html">← 返回ETF轮动</a>
            </div>
        </div>

        <div class="main-layout">
            <div class="sidebar">
                <div class="sidebar-card">
                    <div class="sidebar-title">历史报告</div>
                    {history_links}
                </div>
            </div>
            <div class="content report-content">
                {optimized_content}
            </div>
        </div>
    </div>
</body>
</html>"""


def generate_index_html(reports: list) -> str:
    """生成投资中枢首页（最新报告）- 移动端优化版"""
    if not reports:
        return "<p>暂无报告</p>"

    latest = reports[0]

    # 读取原始内容并解析
    raw_text = latest["path"].read_text(encoding="utf-8")
    original_html = markdown.markdown(raw_text, extensions=MD_EXTENSIONS)
    sections = parse_report_sections(raw_text)
    radar_status = extract_radar_status(raw_text)

    # 生成移动端优化的内容
    optimized_content = generate_mobile_optimized_html(sections, original_html, radar_status)

    # 生成历史报告链接
    history_links = ""
    for r in reports:
        history_links += f'<a href="investment_{r["date_str"]}.html" class="archive-link">{r["date_display"]}</a>\n'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>投资中枢 · 体检报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #f5f6f7;
            color: #131722;
            line-height: 1.6;
        }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}

        .header {{
            background: #fff;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .header h1 {{
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .header-meta {{
            font-size: 13px;
            color: #787b86;
        }}
        .nav {{
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid #e0e3eb;
        }}
        .nav a {{
            display: inline-block;
            padding: 8px 16px;
            margin-right: 8px;
            background: #f0f7ff;
            color: #2962ff;
            text-decoration: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }}
        .nav a:hover {{ background: #e0efff; }}

        .main-layout {{
            display: flex;
            gap: 20px;
        }}
        .sidebar {{
            width: 200px;
            flex-shrink: 0;
        }}
        .content {{
            flex: 1;
            background: #fff;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}

        .sidebar-card {{
            background: #fff;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .sidebar-title {{
            font-size: 12px;
            font-weight: 600;
            color: #787b86;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .archive-link {{
            display: block;
            padding: 8px 10px;
            border-radius: 6px;
            font-size: 13px;
            color: #495057;
            text-decoration: none;
            margin-bottom: 4px;
        }}
        .archive-link:hover {{ background: #f5f6f7; }}
        .archive-link.active {{ background: #e9ecef; font-weight: 600; color: #000; }}

        /* 移动端优化样式 */
        .priority-section {{
            margin-bottom: 20px;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 16px 20px;
            font-size: 16px;
            font-weight: 600;
        }}
        .section-icon {{ font-size: 20px; }}
        .section-header h2 {{
            font-size: 16px;
            font-weight: 600;
            margin: 0;
        }}
        .section-body {{
            padding: 20px;
            background: #fff;
        }}

        .alert-section .section-header {{
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a5a 100%);
            color: #fff;
        }}
        .alert-section .section-body {{
            background: #fff5f5;
            border: 2px solid #ffc9c9;
            border-top: none;
            border-radius: 0 0 12px 12px;
        }}

        .action-section .section-header {{
            background: linear-gradient(135deg, #4dabf7 0%, #339af0 100%);
            color: #fff;
        }}
        .action-section .section-body {{
            background: #f0f7ff;
            border: 2px solid #d0ebff;
            border-top: none;
            border-radius: 0 0 12px 12px;
        }}

        .radar-overview {{
            background: #fff;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .radar-overview .section-header {{
            padding: 0 0 16px 0;
            border-bottom: 1px solid #e0e3eb;
        }}
        .radar-overview .section-header h2 {{ color: #333; }}
        .radar-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            padding-top: 16px;
        }}
        .radar-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 12px 16px;
            background: #f8f9fa;
            border-radius: 8px;
            font-size: 14px;
        }}
        .radar-icon {{ font-size: 18px; }}
        .radar-name {{ font-weight: 500; color: #333; }}
        .radar-desc {{
            margin-left: auto;
            font-size: 12px;
            color: #787b86;
        }}

        .details-section {{ margin-bottom: 20px; }}
        .dim-details {{
            background: #fff;
            border-radius: 12px;
            margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            overflow: hidden;
        }}
        .dim-details summary {{
            padding: 16px 20px;
            font-size: 15px;
            font-weight: 600;
            color: #333;
            cursor: pointer;
            list-style: none;
        }}
        .dim-details summary::before {{
            content: "▶";
            font-size: 12px;
            color: #787b86;
            margin-right: 8px;
            display: inline-block;
            transition: transform 0.2s;
        }}
        .dim-details[open] summary::before {{
            transform: rotate(90deg);
        }}
        .dim-content {{
            padding: 0 20px 20px;
            border-top: 1px solid #f0f3fa;
        }}

        .report-content h1 {{ font-size: 22px; margin-bottom: 16px; }}
        .report-content h2 {{ font-size: 18px; margin-top: 24px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #e0e3eb; }}
        .report-content h3 {{ font-size: 16px; margin-top: 20px; margin-bottom: 10px; }}
        .report-content p {{ margin-bottom: 12px; }}
        .report-content table {{
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
            font-size: 14px;
        }}
        .report-content th {{
            background: #f8f9fa;
            padding: 10px 12px;
            border: 1px solid #e0e3eb;
            text-align: left;
            font-weight: 600;
        }}
        .report-content td {{
            padding: 10px 12px;
            border: 1px solid #e0e3eb;
        }}
        .report-content tr:nth-child(even) {{ background: #fafafa; }}

        @media (max-width: 768px) {{
            .main-layout {{ flex-direction: column; }}
            .sidebar {{ width: 100%; }}
            .radar-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}
        @media (max-width: 480px) {{
            .radar-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 投资中枢 · 体检报告</h1>
            <div class="header-meta">最新报告: {latest["date_display"]} | 数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div class="nav">
                <a href="index.html">← 返回ETF轮动</a>
            </div>
        </div>

        <div class="main-layout">
            <div class="sidebar">
                <div class="sidebar-card">
                    <div class="sidebar-title">历史报告</div>
                    {history_links}
                </div>
            </div>
            <div class="content report-content">
                {optimized_content}
            </div>
        </div>
    </div>
</body>
</html>"""


def git_push():
    """执行git提交和推送"""
    try:
        os.chdir(ETF_DASHBOARD_DIR)

        # 检查是否有变更
        result = subprocess.run(['git', 'status', '--porcelain'],
                              capture_output=True, text=True)
        if not result.stdout.strip():
            print("⚠️ 没有变更需要提交")
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

    # 扫描报告
    print("\n扫描体检报告...")
    reports = get_reports()
    print(f"发现 {len(reports)} 份报告")

    if not reports:
        print("❌ 没有找到体检报告")
        return False

    # 生成首页（最新报告）
    print("\n生成投资中枢首页...")
    index_html = generate_index_html(reports)
    index_path = ETF_DASHBOARD_DIR / "investment.html"
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(index_html)
    print(f"✅ {index_path}")

    # 生成历史报告页面
    print("\n生成历史报告页面...")
    for report in reports:
        html = generate_report_html(report, reports)
        path = ETF_DASHBOARD_DIR / f"investment_{report['date_str']}.html"
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"✅ {report['date_display']} -> {path.name}")

    # Git推送
    print("\n推送到GitHub...")
    if git_push():
        print("\n🎉 导出完成！")
        print(f"投资中枢: https://smiling-jedi.github.io/etf-dashboard-/investment.html")
        return True
    else:
        print("\n⚠️ 页面已生成，但推送失败")
        return False


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)

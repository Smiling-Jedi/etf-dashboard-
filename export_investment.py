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
    """生成单个报告页面HTML"""
    html_content = render_md(report["path"])

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

        /* 报告内容 */
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
        .report-content ul, .report-content ol {{ margin-left: 20px; margin-bottom: 12px; }}
        .report-content li {{ margin-bottom: 6px; }}
        .report-content code {{
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        .report-content pre {{
            background: #f8f9fa;
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
            font-size: 13px;
            margin: 16px 0;
        }}
        .report-content blockquote {{
            border-left: 4px solid #2962ff;
            padding-left: 16px;
            margin: 16px 0;
            color: #555;
        }}

        /* 移动端适配 */
        @media (max-width: 768px) {{
            .main-layout {{ flex-direction: column; }}
            .sidebar {{ width: 100%; }}
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
            <div class="content">
                <div class="report-content">
                    {html_content}
                </div>
            </div>
        </div>
    </div>
</body>
</html>"""


def generate_index_html(reports: list) -> str:
    """生成投资中枢首页（最新报告）"""
    if not reports:
        return "<p>暂无报告</p>"

    latest = reports[0]
    html_content = render_md(latest["path"])

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
        .report-content ul, .report-content ol {{ margin-left: 20px; margin-bottom: 12px; }}
        .report-content li {{ margin-bottom: 6px; }}
        .report-content code {{
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        .report-content pre {{
            background: #f8f9fa;
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
            font-size: 13px;
            margin: 16px 0;
        }}
        .report-content blockquote {{
            border-left: 4px solid #2962ff;
            padding-left: 16px;
            margin: 16px 0;
            color: #555;
        }}

        @media (max-width: 768px) {{
            .main-layout {{ flex-direction: column; }}
            .sidebar {{ width: 100%; }}
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
            <div class="content">
                <div class="report-content">
                    {html_content}
                </div>
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

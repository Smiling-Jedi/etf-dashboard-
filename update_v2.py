#!/usr/bin/env python3
"""
ETF策略独立数据库更新脚本 v2
支持从 data/ 目录读取数据，生成页面
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# 配置
GIT_REPO_PATH = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = Path(GIT_REPO_PATH) / "data"

# ETF池
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}


def load_json(filename):
    """加载JSON文件"""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_html():
    """生成HTML页面"""
    # 加载数据
    trades = load_json('trades.json')
    positions = load_json('positions.json')
    capital = load_json('capital.json')
    weekly = load_json('weekly_scores.json')
    pnl = load_json('pnl_history.json')

    # 获取最新数据
    current_pos = positions.get('current', {})
    latest_week = weekly.get('scores', [{}])[-1] if weekly.get('scores') else {}
    latest_pnl = pnl.get('summary', {})

    # 生成交易记录HTML（只有一条，可点击跳转）
    trade_list = trades.get('trades', [])
    if trade_list:
        t = trade_list[-1]
        trade_html = f'''            <a href="trades.html" class="trade-item-link">
                <div class="trade-date">{t['date']}<br><small>{t['time'][:5]}</small></div>
                <div class="trade-action">{t['action']} {t['name']}</div>
                <div class="trade-price">
                    <div class="price">{t['price']:.4f}</div>
                    <div class="shares">{t['shares']:,}股</div>
                </div>
                <div class="trade-arrow">→</div>
            </a>'''
    else:
        trade_html = '<div class="trade-item"><div style="text-align:center;width:100%;color:#787b86;">暂无交易记录</div></div>'

    # 持仓HTML
    pnl_pct = latest_pnl.get('total_pnl_pct', 0)
    pnl_class = 'pnl-up' if pnl_pct >= 0 else 'pnl-down'
    pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"

    holding_html = f"""        <div class="holding-card">
            <div>
                <div class="holding-label">持仓标的</div>
                <div class="holding-name">{current_pos.get('name', '-')}</div>
                <div class="holding-code">{current_pos.get('code', '-')} · {current_pos.get('total_shares', 0):,}股 · 成本{current_pos.get('avg_cost', 0):.4f}</div>
            </div>
            <div class="holding-pnl">
                <div class="pnl-value {pnl_class}">{pnl_str}</div>
                <div class="holding-label">持仓收益</div>
            </div>
        </div>"""

    # 排名表格
    rankings = latest_week.get('rankings', [])
    rank_rows = []
    rank_colors = ['rank-1', 'rank-2', 'rank-3', 'rank-4']
    for i, r in enumerate(rankings):
        weekly_class = 'change-up' if r.get('weekly_change', 0) >= 0 else 'change-down'
        score_class = 'score-positive' if r.get('score', 0) >= 0 else 'score-negative'
        weekly_str = f"+{r['weekly_change']:.2f}%" if r.get('weekly_change', 0) >= 0 else f"{r['weekly_change']:.2f}%"
        rank_rows.append(f"""                <tr>
                    <td><span class="rank-num {rank_colors[i]}">{r['rank']}</span></td>
                    <td><span class="etf-name">{r['name']}</span><span class="etf-code">{r['code']}</span></td>
                    <td class="change-col"><span class="{weekly_class}">{weekly_str}</span></td>
                    <td style="text-align:right"><span class="score {score_class}">{r['score']:.3f}</span></td>
                </tr>""")
    rank_table = '\n'.join(rank_rows)

    # 计算阈值
    holding_score = latest_week.get('holding_score', 0)
    top_score = latest_week.get('top_score', 0)
    threshold = latest_week.get('threshold', 0)
    should_trade = latest_week.get('should_trade', False)

    if should_trade:
        signal_action, signal_text = "signal-buy", "BUY"
        signal_detail = f"调仓至 <strong>{latest_week.get('top_code', '-')}</strong><br>下周一 14:50 卖出 {current_pos.get('name', '-')}，买入 {latest_week.get('top_code', '-')}"
    else:
        signal_action, signal_text = "signal-hold", "HOLD"
        signal_detail = f"继续持有 <strong>{current_pos.get('name', '-')}<br>下周一无操作"

    calc_html = f"""            <div class="calc-box">
                <div class="calc-row"><span>当前持仓得分</span><span>{holding_score:.3f}</span></div>
                <div class="calc-row"><span>第1名得分</span><span>{top_score:.3f}</span></div>
                <div class="calc-row"><span>阈值条件 (×1.5)</span><span>{holding_score:.3f} × 1.5 = {threshold:.3f}</span></div>
                <div class="calc-row"><span>{top_score:.3f} {'>' if should_trade else '<'} {threshold:.3f} → {'满足' if should_trade else '不满足'}调仓条件</span><span>{'✓ 调仓' if should_trade else '✓ 不调仓'}</span></div>
            </div>"""

    # 汇总统计
    total_pnl = latest_pnl.get('total_pnl', 0)
    total_pnl_str = f"+{total_pnl:.2f}" if total_pnl >= 0 else f"{total_pnl:.2f}"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF轮动策略监控</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f5f5f5; padding: 16px; color: #131722; line-height: 1.5; }}
        .container {{ max-width: 720px; margin: 0 auto; background: #fff; padding: 20px; border-radius: 8px; }}
        .header {{ border-bottom: 1px solid #e0e3eb; padding-bottom: 16px; margin-bottom: 24px; }}
        .header-title {{ font-size: 22px; font-weight: 700; color: #131722; margin-bottom: 8px; }}
        .header-subtitle {{ font-size: 11px; color: #787b86; }}
        .section-title {{ font-size: 14px; font-weight: 600; color: #131722; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .change-up {{ color: #ff5252; }} .change-down {{ color: #00c853; }}
        .change-col {{ text-align: right; font-size: 13px; }}
        .rank-table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
        .rank-table th {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid #e0e3eb; font-size: 12px; font-weight: 400; color: #787b86; }}
        .rank-table td {{ padding: 14px 8px; border-bottom: 1px solid #f0f3fa; font-size: 14px; }}
        .rank-table tr:hover {{ background: #f8f9fd; }}
        .rank-num {{ display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 4px; font-size: 12px; font-weight: 600; color: #fff; }}
        .rank-1 {{ background: #2962ff; }} .rank-2 {{ background: #00c853; }} .rank-3 {{ background: #787b86; }} .rank-4 {{ background: #b7b9c3; }}
        .etf-name {{ font-weight: 500; }} .etf-code {{ font-size: 12px; color: #787b86; margin-left: 4px; }}
        .score {{ font-weight: 600; font-size: 15px; }} .score-positive {{ color: #00c853; }} .score-negative {{ color: #ff5252; }}
        .signal-card {{ border: 1px solid #e0e3eb; border-radius: 4px; padding: 20px; margin-bottom: 24px; text-align: center; }}
        .signal-label {{ font-size: 12px; color: #787b86; margin-bottom: 8px; }}
        .signal-action {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
        .signal-buy {{ color: #00c853; }} .signal-hold {{ color: #ff9800; }} .signal-sell {{ color: #ff5252; }}
        .signal-detail {{ font-size: 13px; color: #787b86; line-height: 1.6; }}
        .calc-box {{ background: transparent; border-radius: 0; padding: 16px 0 0 0; margin-top: 16px; text-align: left; font-size: 13px; color: #131722; border-top: 1px solid #e0e3eb; }}
        .calc-box .calc-row {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
        .calc-box .calc-row:last-child {{ margin-bottom: 0; padding-top: 8px; margin-top: 8px; border-top: 1px dashed #e0e3eb; font-weight: 600; }}
        .holding-card {{ border: 1px solid #e0e3eb; border-radius: 4px; padding: 20px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }}
        .detail-section {{ padding: 0 20px 20px; font-size: 12px; color: #787b86; }}
        .detail-title {{ font-size: 11px; color: #b7b9c3; margin-bottom: 8px; text-transform: uppercase; }}
        .detail-item {{ display: flex; justify-content: space-between; padding: 4px 0; }}
        .holding-label {{ font-size: 12px; color: #787b86; margin-bottom: 4px; }}
        .holding-name {{ font-size: 18px; font-weight: 600; color: #131722; }}
        .holding-code {{ font-size: 13px; color: #787b86; }}
        .holding-pnl {{ text-align: right; }}
        .pnl-value {{ font-size: 24px; font-weight: 700; }} .pnl-up {{ color: #00c853; }} .pnl-down {{ color: #ff5252; }} .pnl-flat {{ color: #787b86; }}
        .trade-section {{ border: 1px solid #e0e3eb; border-radius: 4px; overflow: hidden; }}
        .trade-item-link {{ display: flex; justify-content: space-between; align-items: center; padding: 16px; text-decoration: none; color: #131722; cursor: pointer; transition: background 0.2s; }}
        .trade-item-link:hover {{ background: #f8f9fd; }}
        .trade-item-link .trade-date {{ color: #787b86; font-size: 13px; min-width: 80px; }}
        .trade-item-link .trade-action {{ flex: 1; padding: 0 16px; color: #00c853; font-weight: 500; font-size: 14px; }}
        .trade-item-link .trade-price {{ text-align: right; }}
        .trade-item-link .trade-price .price {{ font-size: 15px; color: #131722; }}
        .trade-item-link .trade-price .shares {{ font-size: 12px; color: #787b86; }}
        .trade-item-link .trade-arrow {{ color: #787b86; margin-left: 8px; }}
        .stats-summary {{ display: flex; justify-content: space-between; padding: 16px 0; border-bottom: 1px solid #f0f3fa; margin-bottom: 12px; }}
        .stat-box {{ text-align: center; }}
        .stat-value {{ font-size: 18px; font-weight: 700; color: #131722; }}
        .stat-label {{ font-size: 11px; color: #787b86; margin-top: 2px; }}
        .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e0e3eb; text-align: center; font-size: 12px; color: #787b86; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">周五盘后打分 | 下周一收盘前10分钟买卖 | 1.5倍阈值</div>
            <div class="header-subtitle">回测: 16.36x | 47.50% | -26.65%回撤 | 夏普1.57 | 99次 | 54.1%</div>
        </div>

        <div class="section-title">当前持仓</div>
{holding_html}

        <div class="section-title">本周评分排名</div>
        <table class="rank-table">
            <thead>
                <tr><th style="width:50px">排名</th><th>ETF</th><th style="width:90px;text-align:right">周涨跌</th><th style="width:90px;text-align:right">得分</th></tr>
            </thead>
            <tbody>
{rank_table}
            </tbody>
        </table>

        <div class="section-title">交易建议</div>
        <div class="signal-card">
            <div class="signal-label">SIGNAL</div>
            <div class="signal-action {signal_action}">{signal_text}</div>
            <div class="signal-detail">{signal_detail}</div>
{calc_html}
        </div>

        <div class="section-title">交易记录</div>
        <div class="trade-section">
{trade_html}
        </div>

        <div class="footer">ETF轮动策略 · 三因子动量模型 · 独立数据库 v2.0</div>
    </div>
</body>
</html>"""
    return html


def generate_trades_html():
    """生成交易记录详情页"""
    trades = load_json('trades.json')
    positions = load_json('positions.json')
    trade_list = trades.get('trades', [])
    current_pos = positions.get('current', {})

    # 生成交易记录行
    trade_rows = ''
    for t in reversed(trade_list):
        trade_rows += f'''            <tr>
                <td class="trade-col-date">{t['date']}</td>
                <td class="trade-col-code">{t['name']}</td>
                <td class="trade-col-action {'action-buy' if t['action'] == '买入' else 'action-sell'}">{t['action']}</td>
                <td class="trade-col-price">{t['price']:.4f}</td>
                <td class="trade-col-shares">{t['shares']:,}</td>
                <td class="trade-col-amount">{int(t['amount'])}</td>
                <td class="trade-col-fee">{t['fee']:.2f}</td>
                <td class="trade-col-note">{t.get('note', '')}</td>
            </tr>'''

    if not trade_rows:
        trade_rows = '<tr><td colspan="8" style="text-align:center;padding:40px;color:#787b86;">暂无交易记录</td></tr>'

    # 计算汇总
    total_buy = sum(t['amount'] for t in trade_list if t['action'] == '买入')
    total_sell = sum(t['amount'] for t in trade_list if t['action'] == '卖出')
    total_fee = sum(t['fee'] for t in trade_list)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>交易记录 - ETF轮动策略</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #fafafa; padding: 16px; color: #131722; line-height: 1.5; }}
        .container {{ max-width: 900px; margin: 0 auto; background: #fff; padding: 20px; border-radius: 8px; }}
        .header {{ border-bottom: 1px solid #e0e3eb; padding-bottom: 16px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
        .back-link {{ color: #2962ff; text-decoration: none; font-size: 14px; }}
        .back-link:hover {{ text-decoration: underline; }}
        h1 {{ font-size: 20px; font-weight: 600; }}
        .summary {{ display: flex; gap: 16px; margin-bottom: 24px; padding: 16px; background: #f8f9fd; border-radius: 4px; flex-wrap: wrap; }}
        .summary-item {{ flex: 1; min-width: 80px; text-align: center; }}
        .summary-value {{ font-size: 16px; font-weight: 700; color: #131722; }}
        .summary-label {{ font-size: 11px; color: #787b86; margin-top: 4px; }}
        .summary-buy {{ color: #00c853; }}
        .summary-sell {{ color: #ff5252; }}
        .trade-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        .trade-table th {{ text-align: left; padding: 12px 8px; border-bottom: 1px solid #e0e3eb; font-size: 12px; font-weight: 500; color: #787b86; white-space: nowrap; }}
        .trade-table td {{ padding: 12px 8px; border-bottom: 1px solid #f0f3fa; }}
        .trade-table tr:hover {{ background: #f8f9fd; }}
        .trade-col-date {{ color: #787b86; font-size: 12px; white-space: nowrap; }}
        .trade-col-code {{ font-weight: 500; }}
        .trade-col-action {{ font-weight: 600; white-space: nowrap; }}
        .action-buy {{ color: #00c853; }}
        .action-sell {{ color: #ff5252; }}
        .trade-col-price, .trade-col-amount {{ text-align: right; font-family: 'SF Mono', monospace; white-space: nowrap; }}
        .trade-col-shares {{ text-align: right; font-family: 'SF Mono', monospace; white-space: nowrap; }}
        .trade-col-fee {{ text-align: right; color: #787b86; font-size: 12px; white-space: nowrap; }}
        .trade-col-note {{ color: #787b86; font-size: 12px; }}
        .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e0e3eb; text-align: center; font-size: 12px; color: #787b86; }}
        @media (max-width: 600px) {{
            body {{ padding: 8px; }}
            .container {{ padding: 12px; border-radius: 4px; }}
            .header {{ flex-direction: column; align-items: flex-start; }}
            .summary {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
            .summary-item {{ min-width: auto; }}
            .summary-value {{ font-size: 14px; }}
            .trade-table {{ font-size: 11px; }}
            .trade-table th, .trade-table td {{ padding: 6px 3px; }}
            .trade-table th:nth-child(2), .trade-table td:nth-child(2) {{ max-width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
                        h1 {{ font-size: 18px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>交易记录</h1>
                <div style="font-size: 12px; color: #787b86; margin-top: 4px;">{current_pos.get('name', '-')} ({current_pos.get('code', '-')})</div>
            </div>
            <a href="index.html" class="back-link">← 返回监控页</a>
        </div>

        <div class="summary">
            <div class="summary-item">
                <div class="summary-value summary-buy">+{total_buy:.2f}</div>
                <div class="summary-label">累计买入</div>
            </div>
            <div class="summary-item">
                <div class="summary-value summary-sell">-{total_sell:.2f}</div>
                <div class="summary-label">累计卖出</div>
            </div>
            <div class="summary-item">
                <div class="summary-value">{total_fee:.2f}</div>
                <div class="summary-label">累计手续费</div>
            </div>
            <div class="summary-item">
                <div class="summary-value">{len(trade_list)}</div>
                <div class="summary-label">交易笔数</div>
            </div>
        </div>

        <table class="trade-table">
            <thead>
                <tr>
                    <th>日期</th>
                    <th>标的</th>
                    <th>操作</th>
                    <th style="text-align:right">价格</th>
                    <th style="text-align:right">数量</th>
                    <th style="text-align:right">金额</th>
                    <th style="text-align:right">手续费</th>
                    <th>备注</th>
                </tr>
            </thead>
            <tbody>
{trade_rows}
            </tbody>
        </table>

        <div class="footer">ETF轮动策略 · 交易记录详情</div>
    </div>
</body>
</html>'''
    return html


def git_push():
    """Git提交和推送"""
    try:
        os.chdir(GIT_REPO_PATH)
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
        if not result.stdout.strip():
            print("⚠️ 没有变更需要提交")
            return True

        subprocess.run(['git', 'add', 'index.html', 'trades.html', 'data/'], check=True)
        today = datetime.now().strftime('%Y-%m-%d')
        subprocess.run(['git', 'commit', '-m', f'Update: {today} ETF data'], check=True)
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)
        print("✅ Git推送成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Git操作失败: {e}")
        return False


def main():
    print("=" * 60)
    print("ETF策略独立数据库更新 v2")
    print("=" * 60)

    # 生成HTML
    html = generate_html()
    trades_html = generate_trades_html()

    # 保存文件
    html_path = os.path.join(GIT_REPO_PATH, 'index.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 主页面已保存: {html_path}")

    trades_path = os.path.join(GIT_REPO_PATH, 'trades.html')
    with open(trades_path, 'w', encoding='utf-8') as f:
        f.write(trades_html)
    print(f"✅ 交易记录页已保存: {trades_path}")

    # Git推送
    print("\n正在推送到GitHub...")
    if git_push():
        print("\n🎉 更新完成！")
        print(f"访问地址: https://smiling-jedi.github.io/etf-dashboard-/")
    else:
        print("\n⚠️ 页面已生成本地，但推送失败")


if __name__ == '__main__':
    main()

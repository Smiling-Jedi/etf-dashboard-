#!/usr/bin/env python3
"""
ETF轮动策略页面自动更新脚本
功能：获取数据 → 计算三因子 → 生成HTML → git推送
用法：python3 update_dashboard.py
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import tushare as ts
from datetime import datetime, timedelta
import subprocess
import os
import sys

# ============ 配置 ============
ETF_POOL = {
    '512890.SH': '红利低波ETF',
    '159949.SZ': '创业板50ETF',
    '513100.SH': '纳指ETF',
    '518880.SH': '黄金ETF'
}

# 三因子参数
BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40

# Git配置
GIT_REPO_PATH = os.path.dirname(os.path.abspath(__file__))


def get_etf_data_tushare(symbol, start_date, end_date):
    """使用tushare获取ETF历史数据"""
    try:
        df = ts.pro_bar(ts_code=symbol, asset='FD',
                        start_date=start_date.replace('-', ''),
                        end_date=end_date.replace('-', ''))
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'vol': 'volume'})
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"❌ 获取 {symbol} 失败: {e}")
        return None


def calc_weekly_change(df):
    """计算周涨跌幅（本周五 vs 上周五）"""
    if df is None or len(df) < 6:
        return 0

    latest = df['close'].iloc[-1]
    # 找上周五（5个交易日前的收盘价）
    if len(df) >= 6:
        last_friday = df['close'].iloc[-6]  # 上周最后一个交易日
    else:
        last_friday = df['close'].iloc[0]

    weekly = (latest / last_friday - 1) * 100
    return weekly


def calc_bias_momentum(close_prices):
    if len(close_prices) < BIAS_N:
        return 0
    ma = close_prices.rolling(window=BIAS_N, min_periods=1).mean()
    bias = close_prices / ma
    if len(bias) < MOMENTUM_DAY:
        return 0
    bias_recent = bias.iloc[-MOMENTUM_DAY:]
    x = np.arange(MOMENTUM_DAY).reshape(-1, 1)
    y = (bias_recent / bias_recent.iloc[0]).values
    lr = LinearRegression()
    lr.fit(x, y)
    return float(lr.coef_[0] * 10000)


def calc_slope_momentum(close_prices):
    if len(close_prices) < SLOPE_N:
        return 0
    prices = close_prices.iloc[-SLOPE_N:]
    normalized_prices = prices / prices.iloc[0]
    x = np.arange(1, SLOPE_N + 1).reshape(-1, 1)
    y = normalized_prices.values
    lr = LinearRegression()
    lr.fit(x, y)
    slope = lr.coef_[0]
    r_squared = lr.score(x, y)
    return float(10000 * slope * r_squared)


def calc_efficiency_momentum(df):
    if len(df) < MOMENTUM_DAY:
        return 0
    df_recent = df.iloc[-MOMENTUM_DAY:].copy()
    pivot = (df_recent['open'] + df_recent['high'] + df_recent['low'] + df_recent['close']) / 4.0
    momentum = 100 * np.log(pivot.iloc[-1] / pivot.iloc[0])
    log_pivot = np.log(pivot)
    direction = abs(log_pivot.iloc[-1] - log_pivot.iloc[0])
    volatility = log_pivot.diff().abs().sum()
    efficiency_ratio = direction / volatility if volatility > 0 else 0
    return float(momentum * efficiency_ratio)


def calc_all_factors(etf_data_dict):
    factors = {}
    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue
        df = etf_data_dict[symbol]
        if len(df) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue

        # 计算周涨跌幅
        weekly = calc_weekly_change(df)

        factors[symbol] = {
            'name': name,
            'code': symbol.split('.')[0],
            'weekly': weekly,
            'bias': calc_bias_momentum(df['close']),
            'slope': calc_slope_momentum(df['close']),
            'efficiency': calc_efficiency_momentum(df)
        }
    return factors


def zscore_normalize(factors):
    if len(factors) < 2:
        return factors
    bias_vals = [f['bias'] for f in factors.values()]
    slope_vals = [f['slope'] for f in factors.values()]
    eff_vals = [f['efficiency'] for f in factors.values()]

    def zscore(vals):
        mean, std = np.mean(vals), np.std(vals)
        if std == 0:
            return [0] * len(vals)
        return [(v - mean) / std for v in vals]

    bias_z = zscore(bias_vals)
    slope_z = zscore(slope_vals)
    eff_z = zscore(eff_vals)

    for i, symbol in enumerate(factors.keys()):
        factors[symbol]['bias_z'] = bias_z[i]
        factors[symbol]['slope_z'] = slope_z[i]
        factors[symbol]['efficiency_z'] = eff_z[i]
        factors[symbol]['total_score'] = (
            WEIGHT_BIAS * bias_z[i] + WEIGHT_SLOPE * slope_z[i] + WEIGHT_EFFICIENCY * eff_z[i]
        )
    return factors


def generate_html(factors, trade_date, next_date, update_time):
    """生成HTML页面"""
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)

    # 生成排名表格行
    rank_rows = []
    rank_colors = ['rank-1', 'rank-2', 'rank-3', 'rank-4']

    for i, (symbol, f) in enumerate(sorted_etfs):
        weekly_class = 'change-up' if f['weekly'] >= 0 else 'change-down'
        score_class = 'score-positive' if f['total_score'] >= 0 else 'score-negative'

        weekly_str = f"+{f['weekly']:.2f}%" if f['weekly'] >= 0 else f"{f['weekly']:.2f}%"

        row = f"""                <tr>
                    <td><span class="rank-num {rank_colors[i]}">{i+1}</span></td>
                    <td><span class="etf-name">{f['name']}</span><span class="etf-code">{f['code']}</span></td>
                    <td class="change-col"><span class="{weekly_class}">{weekly_str}</span></td>
                    <td style="text-align:right"><span class="score {score_class}">{f['total_score']:.3f}</span></td>
                </tr>"""
        rank_rows.append(row)

    rank_table_body = '\n'.join(rank_rows)

    # 获取排名第1的ETF作为建议
    top_etf = sorted_etfs[0][1]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF轮动策略监控</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #fff;
            padding: 24px;
            color: #131722;
            line-height: 1.5;
        }}
        .container {{ max-width: 720px; margin: 0 auto; }}

        /* 头部 */
        .header {{
            border-bottom: 1px solid #e0e3eb;
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .header h1 {{
            font-size: 20px;
            font-weight: 600;
            color: #131722;
            margin-bottom: 4px;
        }}
        .header-meta {{
            font-size: 13px;
            color: #787b86;
        }}
        .header-time {{
            font-size: 13px;
            color: #2962ff;
            font-weight: 500;
            margin-top: 4px;
        }}

        /* 区块标题 */
        .section-title {{
            font-size: 14px;
            font-weight: 600;
            color: #131722;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        /* 涨跌幅颜色 - 红涨绿跌（A股习惯）*/
        .change-up {{ color: #ff5252; }}
        .change-down {{ color: #00c853; }}
        .change-col {{ text-align: right; font-size: 13px; }}

        /* 排名表格 */
        .rank-table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 24px;
        }}
        .rank-table th {{
            text-align: left;
            padding: 10px 8px;
            border-bottom: 1px solid #e0e3eb;
            font-size: 12px;
            font-weight: 400;
            color: #787b86;
        }}
        .rank-table td {{
            padding: 14px 8px;
            border-bottom: 1px solid #f0f3fa;
            font-size: 14px;
        }}
        .rank-table tr:hover {{ background: #f8f9fd; }}
        .rank-num {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            color: #fff;
        }}
        .rank-1 {{ background: #2962ff; }}
        .rank-2 {{ background: #00c853; }}
        .rank-3 {{ background: #787b86; }}
        .rank-4 {{ background: #b7b9c3; }}
        .etf-name {{ font-weight: 500; }}
        .etf-code {{
            font-size: 12px;
            color: #787b86;
            margin-left: 4px;
        }}
        .score {{
            font-weight: 600;
            font-size: 15px;
        }}
        .score-positive {{ color: #00c853; }}
        .score-negative {{ color: #ff5252; }}

        /* 信号卡片 */
        .signal-card {{
            border: 1px solid #e0e3eb;
            border-radius: 4px;
            padding: 20px;
            margin-bottom: 24px;
            text-align: center;
        }}
        .signal-label {{
            font-size: 12px;
            color: #787b86;
            margin-bottom: 8px;
        }}
        .signal-action {{
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        .signal-buy {{ color: #00c853; }}
        .signal-hold {{ color: #ff9800; }}
        .signal-sell {{ color: #ff5252; }}
        .signal-detail {{
            font-size: 13px;
            color: #787b86;
            line-height: 1.6;
        }}

        /* 持仓卡片 */
        .holding-card {{
            border: 1px solid #e0e3eb;
            border-radius: 4px;
            padding: 20px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .holding-label {{
            font-size: 12px;
            color: #787b86;
            margin-bottom: 4px;
        }}
        .holding-name {{
            font-size: 18px;
            font-weight: 600;
            color: #131722;
        }}
        .holding-code {{
            font-size: 13px;
            color: #787b86;
        }}
        .holding-pnl {{
            text-align: right;
        }}
        .pnl-value {{
            font-size: 24px;
            font-weight: 700;
        }}
        .pnl-up {{ color: #00c853; }}
        .pnl-down {{ color: #ff5252; }}
        .pnl-flat {{ color: #787b86; }}

        /* 统计网格 */
        .stats-row {{
            display: flex;
            justify-content: space-between;
            padding: 16px 0;
            border-bottom: 1px solid #f0f3fa;
        }}
        .stat-item:last-child {{ text-align: right; }}
        .stat-value {{
            font-size: 20px;
            font-weight: 700;
            color: #131722;
        }}
        .stat-label {{
            font-size: 12px;
            color: #787b86;
            margin-top: 2px;
        }}

        /* 交易记录 */
        .trade-list {{
            border: 1px solid #e0e3eb;
            border-radius: 4px;
            overflow: hidden;
        }}
        .trade-item {{
            display: flex;
            justify-content: space-between;
            padding: 14px 16px;
            border-bottom: 1px solid #f0f3fa;
            font-size: 14px;
        }}
        .trade-item:last-child {{ border-bottom: none; }}
        .trade-date {{ color: #787b86; font-size: 13px; }}
        .trade-etfs {{ flex: 1; padding: 0 16px; }}
        .trade-out {{ color: #ff5252; }}
        .trade-in {{ color: #00c853; }}
        .trade-arrow {{ color: #787b86; margin: 0 8px; }}
        .trade-price {{ text-align: right; color: #787b86; font-size: 13px; }}

        /* 底部信息 */
        .footer {{
            margin-top: 24px;
            padding-top: 16px;
            border-top: 1px solid #e0e3eb;
            text-align: center;
            font-size: 12px;
            color: #787b86;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <h1>ETF轮动策略</h1>
            <div class="header-meta">评估日期: {trade_date} | 下次: {next_date}</div>
            <div class="header-time">数据更新: {update_time}</div>
        </div>

        <!-- 本周评分排名 -->
        <div class="section-title">本周评分排名</div>
        <table class="rank-table">
            <thead>
                <tr>
                    <th style="width:50px">排名</th>
                    <th>ETF</th>
                    <th style="width:90px;text-align:right">周涨跌</th>
                    <th style="width:90px;text-align:right">得分</th>
                </tr>
            </thead>
            <tbody>
{rank_table_body}
            </tbody>
        </table>

        <!-- 交易建议 -->
        <div class="section-title">交易建议</div>
        <div class="signal-card">
            <div class="signal-label">SIGNAL</div>
            <div class="signal-action signal-buy">BUY</div>
            <div class="signal-detail">
                当前空仓 → 买入{top_etf['name']} ({top_etf['code']})<br>
                目标仓位: 100%
            </div>
        </div>

        <!-- 当前持仓 -->
        <div class="section-title">当前持仓</div>
        <div class="holding-card">
            <div>
                <div class="holding-label">持仓标的</div>
                <div class="holding-name">空仓</div>
                <div class="holding-code">等待建仓</div>
            </div>
            <div class="holding-pnl">
                <div class="pnl-value pnl-flat">0.00%</div>
                <div class="holding-label">持仓收益</div>
            </div>
        </div>

        <!-- 策略表现 -->
        <div class="section-title">策略表现 (2019-2026)</div>
        <div class="stats-row">
            <div class="stat-item">
                <div class="stat-value">17.2x</div>
                <div class="stat-label">7年净值</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">48.6%</div>
                <div class="stat-label">年化收益</div>
            </div>
        </div>
        <div class="stats-row">
            <div class="stat-item">
                <div class="stat-value">-25.4%</div>
                <div class="stat-label">最大回撤</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">99</div>
                <div class="stat-label">交易次数</div>
            </div>
        </div>

        <!-- 交易记录 -->
        <div class="section-title">交易记录</div>
        <div class="trade-list">
            <div class="trade-item">
                <div style="text-align:center;width:100%;color:#787b86;font-size:13px;">暂无交易记录</div>
            </div>
        </div>

        <!-- 底部 -->
        <div class="footer">
            ETF轮动策略 · 三因子动量模型 · 周度评估
        </div>
    </div>
</body>
</html>"""
    return html


def git_push():
    """执行git提交和推送"""
    try:
        os.chdir(GIT_REPO_PATH)

        # 检查是否有变更
        result = subprocess.run(['git', 'status', '--porcelain'],
                              capture_output=True, text=True)
        if not result.stdout.strip():
            print("⚠️ 没有变更需要提交")
            return True

        # git add
        subprocess.run(['git', 'add', 'index.html'], check=True)

        # git commit
        today = datetime.now().strftime('%Y-%m-%d')
        subprocess.run(['git', 'commit', '-m', f'Update: {today} weekly score'], check=True)

        # git push
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)

        print("✅ Git推送成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Git操作失败: {e}")
        return False


def main():
    # 记录开始时间
    start_time = datetime.now()
    update_time = start_time.strftime('%Y-%m-%d %H:%M:%S')

    print("=" * 60)
    print("ETF轮动策略页面自动更新")
    print("=" * 60)
    print(f"开始时间: {update_time}")

    # 计算日期
    today = datetime.now()
    end_date = today.strftime('%Y-%m-%d')
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    next_friday = today + timedelta(days=(4 - today.weekday() + 7) % 7)
    if next_friday <= today:
        next_friday += timedelta(days=7)
    next_date = next_friday.strftime('%Y-%m-%d')

    print(f"\n数据获取范围: {start_date} ~ {end_date}")
    print(f"下次评估日期: {next_date}\n")

    # 获取数据
    print("正在获取ETF数据...")
    etf_data = {}
    for symbol, name in ETF_POOL.items():
        print(f"  获取 {name} ({symbol})...")
        df = get_etf_data_tushare(symbol, start_date, end_date)
        if df is not None:
            etf_data[symbol] = df
            weekly = calc_weekly_change(df)
            print(f"    ✓ {len(df)}条记录 | 周涨跌:{weekly:+.2f}%")

    if len(etf_data) < 4:
        print(f"\n❌ 数据不足 ({len(etf_data)}/4)，无法更新")
        return False

    # 计算因子
    print("\n正在计算三因子得分...")
    factors = calc_all_factors(etf_data)
    factors = zscore_normalize(factors)

    # 显示排名
    print("\n本周排名:")
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    for i, (symbol, f) in enumerate(sorted_etfs, 1):
        weekly_str = f"{f['weekly']:+.2f}%"
        print(f"  {i}. {f['name']} | 周{weekly_str} | 得分: {f['total_score']:.3f}")

    # 生成HTML
    print("\n正在生成HTML页面...")
    html = generate_html(factors, end_date, next_date, update_time)

    # 保存文件
    html_path = os.path.join(GIT_REPO_PATH, 'index.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 页面已保存: {html_path}")

    # Git推送
    print("\n正在推送到GitHub...")
    if git_push():
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n🎉 更新完成！耗时 {elapsed:.1f} 秒")
        print(f"访问地址: https://smiling-jedi.github.io/etf-dashboard-/")
        return True
    else:
        print("\n⚠️ 页面已生成本地，但推送失败")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

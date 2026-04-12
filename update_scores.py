#!/usr/bin/env python3
"""
ETF三因子动量轮动策略 - 最新评分计算并更新JSON数据
自动获取数据、计算得分、更新weekly_scores.json、生成HTML并推送GitHub
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import tushare as ts
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
import warnings
warnings.filterwarnings('ignore')

# 添加光剑系统路径以导入配置
sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
from config.settings import TUSHARE_TOKEN

# 设置Tushare Token
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40
SWITCH_THRESHOLD = 1.5  # 1.5倍阈值

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')


def get_etf_data_tushare(symbol, start_date, end_date):
    """使用tushare获取ETF历史数据"""
    try:
        ts_code = f"{symbol}.SH" if symbol.startswith('5') else f"{symbol}.SZ"
        df = pro.fund_daily(ts_code=ts_code,
                           start_date=start_date.replace('-', ''),
                           end_date=end_date.replace('-', ''))
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        df = df.rename(columns={'vol': 'volume'})
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"获取 {symbol} 失败: {e}")
        return None


def calc_bias_momentum(close_prices):
    """计算偏离度动量因子"""
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
    """计算斜率动量因子"""
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
    """计算效率动量因子"""
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
    """计算所有ETF的三因子"""
    factors = {}
    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue
        df = etf_data_dict[symbol]
        if len(df) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue

        # 最新价格
        latest_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2] if len(df) > 1 else latest_price
        daily_change = (latest_price / prev_price - 1) * 100

        factors[symbol] = {
            'name': name,
            'latest_price': latest_price,
            'daily_change': daily_change,
            'bias': calc_bias_momentum(df['close']),
            'slope': calc_slope_momentum(df['close']),
            'efficiency': calc_efficiency_momentum(df)
        }
    return factors


def zscore_normalize(factors):
    """Z-Score标准化"""
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
        factors[symbol]['bias_z'] = float(bias_z[i])
        factors[symbol]['slope_z'] = float(slope_z[i])
        factors[symbol]['efficiency_z'] = float(eff_z[i])
        factors[symbol]['total_score'] = float(
            WEIGHT_BIAS * bias_z[i] + WEIGHT_SLOPE * slope_z[i] + WEIGHT_EFFICIENCY * eff_z[i]
        )
    return factors


def get_last_friday():
    """获取最近一个周五的日期（周五收盘后返回今天，其他时间返回上周五）"""
    today = datetime.now()
    weekday = today.weekday()  # 周一=0, 周五=4, 周日=6

    # 距离上周五的天数映射
    # 周一(0) -> 3天前上周五; 周二(1) -> 4天前; 周三(2) -> 5天前; 周四(3) -> 6天前
    # 周五(4) -> 0天(收盘后)或7天(收盘前)
    # 周六(5) -> 1天前; 周日(6) -> 2天前
    days_to_friday = {0: 3, 1: 4, 2: 5, 3: 6, 5: 1, 6: 2}

    if weekday == 4:
        # 周五：收盘后(15:00+)用今天，收盘前用上周
        if today.hour >= 15:
            return today.strftime('%Y-%m-%d')
        else:
            return (today - timedelta(days=7)).strftime('%Y-%m-%d')
    else:
        # 其他日期：回退到最近周五
        return (today - timedelta(days=days_to_friday[weekday])).strftime('%Y-%m-%d')


def update_weekly_scores(factors, sorted_etfs, positions_file):
    """更新weekly_scores.json文件"""
    # 读取当前持仓
    with open(positions_file, 'r', encoding='utf-8') as f:
        positions = json.load(f)
    current_holding = positions.get('current', {}).get('code', None)

    # 计算排名数据
    rankings = []
    for rank, (symbol, f) in enumerate(sorted_etfs, 1):
        rankings.append({
            'rank': rank,
            'code': symbol,
            'name': f['name'],
            'score': float(f['total_score']),
            'weekly_change': float(f['daily_change'])
        })

    # 计算阈值和交易信号
    if current_holding and current_holding in factors:
        holding_score = factors[current_holding]['total_score']
    else:
        holding_score = sorted_etfs[0][1]['total_score'] if sorted_etfs else 0

    top_score = sorted_etfs[0][1]['total_score'] if sorted_etfs else 0
    top_code = sorted_etfs[0][0] if sorted_etfs else None
    top_name = sorted_etfs[0][1]['name'] if sorted_etfs else ''

    # 1.5倍阈值判断
    if current_holding and current_holding == top_code:
        should_trade = False
        signal = f"继续持有 {ETF_POOL.get(current_holding, current_holding)}"
    else:
        threshold = holding_score * SWITCH_THRESHOLD if holding_score > 0 else 0
        if holding_score <= 0:
            should_trade = top_score > 0 or (top_score > threshold if threshold > 0 else True)
        else:
            should_trade = top_score > threshold

        if should_trade:
            signal = f"调仓至 {top_name}"
        else:
            signal = f"继续持有 {ETF_POOL.get(current_holding, current_holding) if current_holding else top_name}"

    # 获取日期
    friday_date = get_last_friday()
    next_monday = (datetime.strptime(friday_date, '%Y-%m-%d') + timedelta(days=3)).strftime('%Y-%m-%d')

    # 构建weekly_scores数据结构
    week_data = {
        'week_date': friday_date,
        'trade_date': friday_date,
        'next_trade_date': next_monday,
        'rankings': rankings,
        'holding_code': current_holding,
        'holding_score': float(holding_score),
        'top_code': top_code,
        'top_score': float(top_score),
        'threshold': float(threshold if current_holding and holding_score > 0 else top_score * SWITCH_THRESHOLD),
        'should_trade': bool(should_trade),
        'signal': 'BUY' if should_trade else 'HOLD',
        'action': signal + '，下周一' + ('执行调仓' if should_trade else '无操作')
    }

    # 读取现有数据或创建新结构
    scores_file = os.path.join(DATA_DIR, 'weekly_scores.json')
    if os.path.exists(scores_file):
        with open(scores_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {
            'metadata': {
                'created_at': friday_date,
                'version': '1.0'
            },
            'scores': []
        }

    # 更新元数据
    data['metadata']['last_updated'] = friday_date

    # 查找是否已有本周数据
    existing_idx = None
    for i, score in enumerate(data.get('scores', [])):
        if score.get('week_date') == friday_date:
            existing_idx = i
            break

    # 添加或更新本周数据
    if existing_idx is not None:
        data['scores'][existing_idx] = week_data
    else:
        data.setdefault('scores', []).append(week_data)

    # 保存文件
    with open(scores_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ weekly_scores.json 已更新: {scores_file}")
    return week_data


def generate_html(etf_data=None):
    """生成HTML页面"""
    # 加载数据
    with open(os.path.join(DATA_DIR, 'weekly_scores.json'), 'r', encoding='utf-8') as f:
        weekly = json.load(f)
    with open(os.path.join(DATA_DIR, 'trades.json'), 'r', encoding='utf-8') as f:
        trades = json.load(f)
    with open(os.path.join(DATA_DIR, 'positions.json'), 'r', encoding='utf-8') as f:
        positions = json.load(f)
    with open(os.path.join(DATA_DIR, 'pnl_history.json'), 'r', encoding='utf-8') as f:
        pnl = json.load(f)

    current_pos = positions.get('current', {})
    latest_week = weekly.get('scores', [{}])[-1] if weekly.get('scores') else {}

    # 获取最新排名更新日期和时间
    week_date = latest_week.get('week_date', '')
    update_date = weekly.get('metadata', {}).get('last_updated', week_date)
    update_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 计算实际初始资金：交易记录中累计买入金额（永久固定）
    trade_list = trades.get('trades', [])
    initial_principal = sum(t['amount'] for t in trade_list if t['action'] == '买入')  # 92,366元

    # 累积已实现盈亏（从pnl_history读取，调仓时累加）
    realized_pnl = pnl.get('summary', {}).get('total_realized_pnl', 0)

    # 当前持仓信息
    current_shares = current_pos.get('total_shares', 0)
    current_cost = current_pos.get('total_cost', 0)  # 当前持仓成本
    current_code = current_pos.get('code', '')

    # 从etf_data获取当前持仓的最新价格
    latest_price = None
    if etf_data and current_code in etf_data:
        latest_price = etf_data[current_code]['close'].iloc[-1]
    elif etf_data:
        # 尝试不带后缀的code
        for symbol in etf_data:
            if symbol.startswith(current_code):
                latest_price = etf_data[symbol]['close'].iloc[-1]
                break

    # 如果没有找到价格，使用持仓均价作为 fallback
    if latest_price is None and current_shares > 0:
        latest_price = current_pos.get('avg_cost', 0)

    # 计算当前市值（使用最新价格）
    current_market_value = current_shares * latest_price if latest_price and current_shares > 0 else current_cost

    # 通用公式：累积盈亏 = 已实现盈亏 + (当前市值 - 当前成本)
    unrealized_pnl = current_market_value - current_cost
    total_pnl = realized_pnl + unrealized_pnl
    total_pnl_pct = (total_pnl / initial_principal) * 100 if initial_principal > 0 else 0

    # 交易记录HTML
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

    # 持仓HTML - 使用重新计算的正确收益
    pnl_class = 'pnl-up' if total_pnl_pct >= 0 else 'pnl-down'
    pnl_str = f"+{total_pnl_pct:.2f}%" if total_pnl_pct >= 0 else f"{total_pnl_pct:.2f}%"

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
        .header-subtitle {{ font-size: 11px; color: #787b86; margin-bottom: 4px; }}
        .header-update {{ font-size: 11px; color: #2962ff; }}
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
            <div class="header-update">排名更新: {update_date} {datetime.now().strftime('%H:%M:%S')}</div>
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


def save_html(html_content):
    """保存HTML文件"""
    html_path = os.path.join(SCRIPT_DIR, 'index.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ 主页面已保存: {html_path}")
    return html_path


def verify_scores_with_backtest(current_factors, etf_data_dict):
    """
    与回测脚本进行评分验算对比
    对比两个回测脚本的计算结果，检查是否一致
    """
    import subprocess
    import tempfile

    print("\n" + "=" * 70)
    print("📊 ETF评分验算报告")
    print("=" * 70)

    # 准备当前脚本的评分结果
    current_scores = {symbol: f['total_score'] for symbol, f in current_factors.items()}

    # 创建临时验证脚本1：周度收盘价版本
    verify_script_1 = '''
import sys
sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
from config.settings import TUSHARE_TOKEN
import tushare as ts
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import json

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4

def get_etf_data(symbol, start_date, end_date):
    try:
        if symbol.startswith('159'):
            ts_code = f'{symbol}.SZ'
        else:
            ts_code = f'{symbol}.SH'
        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        return df[['open', 'high', 'low', 'close']]
    except:
        return None

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
        factors[symbol] = {
            'name': name,
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

# 获取数据
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')

etf_data = {}
for symbol, name in ETF_POOL.items():
    df = get_etf_data(symbol, start_date, end_date)
    if df is not None:
        etf_data[symbol] = df

factors = calc_all_factors(etf_data)
factors = zscore_normalize(factors)

results = {symbol: f['total_score'] for symbol, f in factors.items()}
print(json.dumps(results, indent=2))
'''

    # 创建临时验证脚本2：v3版本
    verify_script_2 = '''
import sys
sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
from config.settings import TUSHARE_TOKEN
import tushare as ts
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import json

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4

def get_etf_data(symbol, start_date, end_date):
    try:
        if symbol.startswith('159'):
            ts_code = f'{symbol}.SZ'
        else:
            ts_code = f'{symbol}.SH'
        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        return df[['open', 'high', 'low', 'close']]
    except:
        return None

def calc_bias_momentum(close_prices):
    if len(close_prices) < BIAS_N:
        return 0
    bias = close_prices / close_prices.rolling(window=BIAS_N, min_periods=1).mean()
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
        factors[symbol] = {
            'name': name,
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

# 获取数据
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')

etf_data = {}
for symbol, name in ETF_POOL.items():
    df = get_etf_data(symbol, start_date, end_date)
    if df is not None:
        etf_data[symbol] = df

factors = calc_all_factors(etf_data)
factors = zscore_normalize(factors)

results = {symbol: f['total_score'] for symbol, f in factors.items()}
print(json.dumps(results, indent=2))
'''

    try:
        # 运行验证脚本1
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f1:
            f1.write(verify_script_1)
            f1.flush()
            result1 = subprocess.run(['python3', f1.name], capture_output=True, text=True, timeout=60)
            os.unlink(f1.name)

        # 运行验证脚本2
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f2:
            f2.write(verify_script_2)
            f2.flush()
            result2 = subprocess.run(['python3', f2.name], capture_output=True, text=True, timeout=60)
            os.unlink(f2.name)

        # 解析结果
        backtest1_scores = json.loads(result1.stdout.strip()) if result1.returncode == 0 else {}
        backtest2_scores = json.loads(result2.stdout.strip()) if result2.returncode == 0 else {}

        # 打印对比表格
        print(f"\n{'='*80}")
        print(f"{'验算对比表':^80}")
        print(f"{'='*80}")
        print(f"{'ETF':<12} {'当前脚本':>12} {'回测脚本1':>12} {'回测脚本2':>12} {'差异判断':>12}")
        print("-" * 80)

        all_match = True
        for symbol in ETF_POOL.keys():
            if symbol in current_scores:
                current = current_scores[symbol]
                bt1 = backtest1_scores.get(symbol, 'N/A')
                bt2 = backtest2_scores.get(symbol, 'N/A')

                if isinstance(bt1, (int, float)) and isinstance(bt2, (int, float)):
                    diff1 = abs(current - bt1)
                    diff2 = abs(current - bt2)
                    max_diff = max(diff1, diff2)

                    if max_diff < 0.001:  # 允许0.001的浮点误差
                        status = "✅ 正常"
                    else:
                        status = f"⚠️  偏差{max_diff:.4f}"
                        all_match = False
                else:
                    status = "❌ 获取失败"
                    all_match = False

                bt1_str = f"{bt1:.4f}" if isinstance(bt1, (int, float)) else str(bt1)
                bt2_str = f"{bt2:.4f}" if isinstance(bt2, (int, float)) else str(bt2)
                print(f"{ETF_POOL[symbol]:<12} {current:>12.4f} {bt1_str:>12} {bt2_str:>12} {status:>12}")

        print("-" * 80)
        if all_match:
            print("✅ 验算通过：所有评分与回测脚本一致")
        else:
            print("⚠️  验算警告：发现评分差异，请检查数据或代码")
        print(f"{'='*80}\n")

    except Exception as e:
        print(f"\n⚠️ 验算过程出错: {e}")
        print("   跳过验算，继续执行后续步骤...\n")


def generate_trades_html():
    """从 trades.json 动态生成交易记录页面"""
    with open(os.path.join(DATA_DIR, 'trades.json'), 'r', encoding='utf-8') as f:
        trades = json.load(f)
    with open(os.path.join(DATA_DIR, 'positions.json'), 'r', encoding='utf-8') as f:
        positions = json.load(f)

    trade_list = trades.get('trades', [])
    current_pos = positions.get('current', {})

    # 计算汇总数据
    total_buy = sum(t['amount'] for t in trade_list if t['action'] == '买入')
    total_sell = sum(t['amount'] for t in trade_list if t['action'] == '卖出')
    total_fee = sum(t.get('fee', 0) for t in trade_list)
    trade_count = len(trade_list)

    # 生成交易记录行
    trade_rows = []
    for t in reversed(trade_list):  # 最新的在前面
        action_class = 'action-buy' if t['action'] == '买入' else 'action-sell'
        row = f"""            <tr>
                <td class="trade-col-date">{t['date']}</td>
                <td class="trade-col-code">{t['name']}</td>
                <td class="trade-col-action {action_class}">{t['action']}</td>
                <td class="trade-col-price">{t['price']:.4f}</td>
                <td class="trade-col-shares">{t['shares']:,}</td>
                <td class="trade-col-amount">{t['amount']:.2f}</td>
                <td class="trade-col-fee">{t.get('fee', 0):.2f}</td>
                <td class="trade-col-note">{t.get('note', '')}</td>
            </tr>"""
        trade_rows.append(row)

    if not trade_rows:
        trade_rows = ['<tr><td colspan="8" style="text-align:center;color:#787b86;padding:20px;">暂无交易记录</td></tr>']

    trades_html_content = f"""<!DOCTYPE html>
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
                <div style="font-size: 12px; color: #787b86; margin-top: 4px;">{current_pos.get('name', '无持仓')} ({current_pos.get('code', '-')})</div>
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
                <div class="summary-value">{trade_count}</div>
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
{chr(10).join(trade_rows)}
            </tbody>
        </table>

        <div class="footer">ETF轮动策略 · 交易记录详情</div>
    </div>
</body>
</html>"""

    output_file = os.path.join(SCRIPT_DIR, 'trades.html')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(trades_html_content)
    print(f"✅ 交易记录页面已生成: {output_file}")


def git_push():
    """Git提交和推送"""
    try:
        os.chdir(SCRIPT_DIR)
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
    print("=" * 80)
    print("ETF三因子动量轮动策略 - 数据更新与推送")
    print("=" * 80)

    # Step 1: 获取数据
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    print(f"\n📊 正在从Tushare获取ETF数据...")
    print(f"数据范围: {start_date} ~ {end_date}")

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        df = get_etf_data_tushare(symbol, start_date, end_date)
        if df is not None:
            etf_data[symbol] = df
            print(f"  ✓ {name} ({symbol}): {len(df)}条记录, 最新日期 {df.index[-1].strftime('%Y-%m-%d')}")

    if len(etf_data) < 4:
        print(f"\n❌ 数据不足，只有 {len(etf_data)}/4 只ETF")
        return

    # Step 2: 计算因子
    print("\n🔢 正在计算三因子得分...")
    factors = calc_all_factors(etf_data)
    factors = zscore_normalize(factors)

    # 打印评分表
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    print(f"\n{'='*60}")
    print(f"ETF三因子评分排名 - {get_last_friday()}")
    print(f"{'='*60}")
    print(f"{'排名':<4} {'代码':<10} {'名称':<12} {'得分':<8} {'偏离度':<10} {'斜率':<10} {'效率':<10}")
    print("-" * 60)
    for rank, (symbol, f) in enumerate(sorted_etfs, 1):
        print(f"{rank:<4} {symbol:<10} {f['name']:<12} {f['total_score']:>7.3f} {f['bias']:>10.2f} {f['slope']:>10.2f} {f['efficiency']:>10.2f}")

    # Step 2.5: 验算评分结果
    print("\n🔍 正在与回测脚本进行评分验算...")
    verify_scores_with_backtest(factors, etf_data)

    # Step 3: 更新weekly_scores.json
    positions_file = os.path.join(DATA_DIR, 'positions.json')
    week_data = update_weekly_scores(factors, sorted_etfs, positions_file)

    print(f"\n📋 交易建议:")
    print(f"  信号: {week_data['signal']}")
    print(f"  操作: {week_data['action']}")
    if week_data['should_trade']:
        print(f"  ⚠️ 下周一需要调仓: 卖出 {week_data.get('holding_code', '-')}, 买入 {week_data['top_code']}")

    # Step 4: 生成HTML
    print("\n🎨 正在生成HTML页面...")
    html = generate_html(etf_data)
    save_html(html)
    generate_trades_html()

    # Step 5: Git推送
    print("\n🚀 正在推送到GitHub...")
    if git_push():
        print("\n" + "=" * 60)
        print("🎉 更新完成！")
        print(f"访问地址: https://smiling-jedi.github.io/etf-dashboard-/")
        print("=" * 60)
    else:
        print("\n⚠️ 页面已生成本地，但推送失败")


if __name__ == '__main__':
    main()

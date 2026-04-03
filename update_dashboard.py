#!/usr/bin/env python3
"""
ETF轮动策略页面自动更新脚本
功能：获取数据 → 计算三因子 → 生成HTML → git推送
用法：python3 update_dashboard.py
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import subprocess
import os
import sys
import re
from pathlib import Path

# 添加项目路径，导入配置
sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
from config.settings import TUSHARE_TOKEN

# ============ 配置 ============
ETF_POOL = {
    '512890.SH': '红利低波ETF',
    '159949.SZ': '创业板50ETF',
    '513100.SH': '纳指ETF',
    '518880.SH': '黄金ETF'
}

CODE_TO_SYMBOL = {v: k for k, v in ETF_POOL.items()}

# 三因子参数
BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40

# Git配置
GIT_REPO_PATH = os.path.dirname(os.path.abspath(__file__))

# 交易记录目录
MEMORY_DIR = Path("/Users/jediyang/.claude/projects/-Users-jediyang-ClaudeCode-Project-Makemoney/memory")

# OpenD配置
OPEND_HOST = os.environ.get('OPEND_HOST', '127.0.0.1')
OPEND_PORT = int(os.environ.get('OPEND_PORT', '11111'))


def get_etf_data_tushare(symbol, start_date, end_date):
    """使用tushare获取ETF历史数据"""
    try:
        import tushare as ts

        # 使用项目配置的token
        ts.set_token(TUSHARE_TOKEN)

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
        print(f"    ⚠️ Tushare失败: {e}")
        return None


def get_etf_data_akshare(symbol, start_date, end_date):
    """使用akshare获取ETF历史数据"""
    try:
        import akshare as ak
        code = symbol.split('.')[0]

        # 获取ETF历史数据
        df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                  start_date=start_date.replace('-', ''),
                                  end_date=end_date.replace('-', ''),
                                  adjust="qfq")
        if df is None or df.empty:
            return None

        df = df.sort_values('日期')
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.set_index('日期')
        df = df.rename(columns={
            '开盘': 'open', '最高': 'high', '最低': 'low',
            '收盘': 'close', '成交量': 'volume'
        })
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"    ⚠️ AKShare失败: {e}")
        return None


def get_etf_data_opend(symbol, start_date, end_date):
    """使用富途OpenD获取ETF历史数据"""
    try:
        from futu import OpenQuoteContext

        code = symbol.split('.')[0]
        # 确定市场
        if symbol.endswith('.SH'):
            market = 'SH'
        elif symbol.endswith('.SZ'):
            market = 'SZ'
        else:
            return None

        quote_ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)

        ret, data, page_req_key = quote_ctx.request_history_kline(
            f"{market}.{code}",
            start=start_date,
            end=end_date,
            ktype='K_DAY'
        )

        quote_ctx.close()

        if ret != 0 or data is None or data.empty:
            return None

        data = data.sort_values('time_key')
        data['time_key'] = pd.to_datetime(data['time_key'])
        data = data.set_index('time_key')
        data = data.rename(columns={
            'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'volume': 'volume'
        })
        return data[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"    ⚠️ OpenD失败: {e}")
        return None


def get_etf_data(symbol, start_date, end_date):
    """获取ETF数据，按优先级尝试多个数据源"""
    # 尝试1: Tushare
    print(f"    尝试 Tushare...")
    df = get_etf_data_tushare(symbol, start_date, end_date)
    if df is not None and not df.empty:
        print(f"    ✓ Tushare成功")
        return df

    # 尝试2: AKShare
    print(f"    尝试 AKShare...")
    df = get_etf_data_akshare(symbol, start_date, end_date)
    if df is not None and not df.empty:
        print(f"    ✓ AKShare成功")
        return df

    # 尝试3: OpenD
    print(f"    尝试 OpenD...")
    df = get_etf_data_opend(symbol, start_date, end_date)
    if df is not None and not df.empty:
        print(f"    ✓ OpenD成功")
        return df

    print(f"    ❌ 所有数据源都失败")
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


def parse_trade_records():
    """解析交易记录文件，返回持仓状态"""
    trades = []

    if not MEMORY_DIR.exists():
        return trades

    # 查找所有交易记录文件
    trade_files = list(MEMORY_DIR.glob("trade_*.md"))

    for f in trade_files:
        try:
            text = f.read_text(encoding='utf-8')

            # 解析交易日期
            date_match = re.search(r'\*\*交易日期\*\*[:：]\s*(\d{4}-\d{2}-\d{2})', text)
            if not date_match:
                continue
            trade_date = date_match.group(1)

            # 解析标的
            code_match = re.search(r'\*\*标的\*\*[:：]\s*(\d+)\s+(.+)', text)
            if not code_match:
                continue
            code = code_match.group(1)
            name = code_match.group(2).strip()

            # 解析操作
            action_match = re.search(r'\*\*操作\*\*[:：]\s*(\w+)', text)
            action = action_match.group(1) if action_match else "买入"

            # 解析价格
            price_match = re.search(r'\*\*价格\*\*[:：]\s*([\d.]+)', text)
            if not price_match:
                continue
            price = float(price_match.group(1))

            # 解析数量
            qty_match = re.search(r'\*\*数量\*\*[:：]\s*([\d,]+)', text)
            if not qty_match:
                continue
            quantity = int(qty_match.group(1).replace(',', ''))

            trades.append({
                'date': trade_date,
                'code': code,
                'name': name,
                'action': action,
                'price': price,
                'quantity': quantity
            })
        except Exception as e:
            print(f"  ⚠️ 解析 {f.name} 失败: {e}")
            continue

    # 按日期排序
    trades.sort(key=lambda x: x['date'])
    return trades


def calc_position(trades, etf_data_dict):
    """计算当前持仓和收益"""
    if not trades:
        return None

    # 汇总持仓（假设只有买入，简单处理）
    positions = {}
    for t in trades:
        code = t['code']
        if code not in positions:
            positions[code] = {'quantity': 0, 'cost': 0, 'name': t['name']}

        if t['action'] == '买入':
            # 计算加权平均成本
            old_qty = positions[code]['quantity']
            old_cost = positions[code]['cost']
            new_qty = t['quantity']
            new_price = t['price']

            total_cost = old_qty * old_cost + new_qty * new_price
            total_qty = old_qty + new_qty

            positions[code]['quantity'] = total_qty
            positions[code]['cost'] = total_cost / total_qty if total_qty > 0 else 0

    # 获取最新价格计算收益
    position_list = []
    for code, pos in positions.items():
        # 找到对应的symbol
        symbol = None
        for sym, name in ETF_POOL.items():
            if code in sym:
                symbol = sym
                break

        if symbol and symbol in etf_data_dict:
            latest_price = etf_data_dict[symbol]['close'].iloc[-1]
            cost = pos['cost']
            pnl_pct = (latest_price / cost - 1) * 100 if cost > 0 else 0

            position_list.append({
                'code': code,
                'name': pos['name'],
                'quantity': pos['quantity'],
                'cost': cost,
                'price': latest_price,
                'pnl_pct': pnl_pct
            })

    # 返回第一个持仓（假设单持仓策略）
    return position_list[0] if position_list else None


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


def generate_html(factors, trade_date, next_date, update_time, position=None, trades=None):
    """生成HTML页面 - 策略3收盘版布局"""
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    top_etf = sorted_etfs[0][1]
    top_score = top_etf['total_score']

    # 生成排名表格
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

    # 持仓和信号计算
    if position:
        current_score = next((f['total_score'] for sym, f in factors.items() if f['code'] == position['code']), 0)
        threshold = current_score * 1.5
        should_trade = top_score > threshold
        pnl_class = 'pnl-up' if position['pnl_pct'] >= 0 else 'pnl-down'
        pnl_str = f"+{position['pnl_pct']:.2f}%" if position['pnl_pct'] >= 0 else f"{position['pnl_pct']:.2f}%"
        holding_html = f"""        <div class="holding-card">
            <div>
                <div class="holding-label">持仓标的</div>
                <div class="holding-name">{position['name']}</div>
                <div class="holding-code">{position['code']} · {position['quantity']:,}股 · 成本{position['cost']:.4f}</div>
            </div>
            <div class="holding-pnl">
                <div class="pnl-value {pnl_class}">{pnl_str}</div>
                <div class="holding-label">持仓收益</div>
            </div>
        </div>"""
        if should_trade:
            signal_action, signal_text = "signal-buy", "BUY"
            signal_detail = f"调仓至 <strong>{top_etf['name']}</strong><br>下周一 (4月6日) 14:50 卖出 {position['name']}，买入 {top_etf['name']}"
        else:
            signal_action, signal_text = "signal-hold", "HOLD"
            signal_detail = f"继续持有 <strong>{position['name']}</strong><br>下周一 (4月6日) 无操作"
        calc_html = f"""            <div class="calc-box">
                <div class="calc-row"><span>当前持仓得分</span><span>{current_score:.3f}</span></div>
                <div class="calc-row"><span>第1名得分</span><span>{top_score:.3f}</span></div>
                <div class="calc-row"><span>阈值条件 (×1.5)</span><span>{current_score:.3f} × 1.5 = {threshold:.3f}</span></div>
                <div class="calc-row"><span>{top_score:.3f} {'>' if should_trade else '<'} {threshold:.3f} → {'满足' if should_trade else '不满足'}调仓条件</span><span>{'✓ 调仓' if should_trade else '✓ 不调仓'}</span></div>
            </div>"""
    else:
        holding_html = f"""        <div class="holding-card">
            <div>
                <div class="holding-label">持仓标的</div>
                <div class="holding-name">空仓</div>
                <div class="holding-code">等待建仓</div>
            </div>
            <div class="holding-pnl">
                <div class="pnl-value pnl-flat">0.00%</div>
                <div class="holding-label">持仓收益</div>
            </div>
        </div>"""
        signal_action, signal_text = "signal-buy", "BUY"
        signal_detail = f"当前空仓 → 买入<strong>{top_etf['name']}</strong><br>下周一 (4月6日) 14:50 建仓"
        calc_html = ""

    # 交易记录 - 只显示最近1条
    if trades and len(trades) > 0:
        t = trades[-1]
        trade_list_html = f"""            <div class="trade-item">
                <div class="trade-date">{t['date']}</div>
                <div class="trade-etfs"><span class="trade-in">买入 {t['name']}</span></div>
                <div class="trade-price">{t['price']:.4f}<br><small>{t['quantity']:,}股</small></div>
            </div>"""
        if len(trades) > 1:
            more_trades = ''.join([f"""            <div class="trade-item" style="border-bottom:1px solid #f0f3fa;padding:14px 0;">
                <div class="trade-date">{t['date']}</div>
                <div class="trade-etfs"><span class="trade-in">买入 {t['name']}</span></div>
                <div class="trade-price">{t['price']:.4f}<br><small>{t['quantity']:,}股</small></div>
            </div>""" for t in trades[:-1][::-1]])
            trade_list_html += f"""
            <div class="hidden-trades" id="more-trades">
{more_trades}
            </div>
            <button class="show-more" onclick="toggleTrades(this)">查看更多记录 ▼</button>"""
    else:
        trade_list_html = '            <div class="trade-item"><div style="text-align:center;width:100%;color:#787b86;font-size:13px;">暂无交易记录</div></div>'

    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF轮动策略监控</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #fff; padding: 24px; color: #131722; line-height: 1.5; }}
        .container {{ max-width: 720px; margin: 0 auto; }}
        .header {{ border-bottom: 1px solid #e0e3eb; padding-bottom: 16px; margin-bottom: 24px; }}
        .strategy-badge {{ display: inline-flex; align-items: center; gap: 6px; background: #e3f2fd; color: #1976d2; font-size: 11px; font-weight: 500; padding: 4px 10px; border-radius: 12px; margin-bottom: 8px; }}
        .strategy-stats {{ font-size: 11px; color: #b7b9c3; margin-top: 8px; padding-top: 8px; border-top: 1px solid #f0f3fa; }}
        .header-meta {{ font-size: 13px; color: #787b86; margin-top: 4px; }}
        .header-time {{ font-size: 13px; color: #2962ff; font-weight: 500; margin-top: 4px; }}
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
        .calc-box {{ background: #f8f9fd; border-radius: 4px; padding: 12px 16px; margin-top: 16px; text-align: left; font-size: 12px; color: #787b86; }}
        .calc-box .calc-row {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
        .calc-box .calc-row:last-child {{ margin-bottom: 0; padding-top: 8px; margin-top: 8px; border-top: 1px dashed #e0e3eb; font-weight: 500; color: #131722; }}
        .holding-card {{ border: 1px solid #e0e3eb; border-radius: 4px; padding: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }}
        .holding-label {{ font-size: 12px; color: #787b86; margin-bottom: 4px; }}
        .holding-name {{ font-size: 18px; font-weight: 600; color: #131722; }}
        .holding-code {{ font-size: 13px; color: #787b86; }}
        .holding-pnl {{ text-align: right; }}
        .pnl-value {{ font-size: 24px; font-weight: 700; }} .pnl-up {{ color: #00c853; }} .pnl-down {{ color: #ff5252; }} .pnl-flat {{ color: #787b86; }}
        .trade-list {{ border: 1px solid #e0e3eb; border-radius: 4px; overflow: hidden; }}
        .trade-item {{ display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid #f0f3fa; font-size: 14px; }}
        .trade-item:last-child {{ border-bottom: none; }}
        .trade-date {{ color: #787b86; font-size: 13px; min-width: 80px; }}
        .trade-etfs {{ flex: 1; padding: 0 16px; }}
        .trade-in {{ color: #00c853; }}
        .trade-price {{ text-align: right; color: #787b86; font-size: 13px; }}
        .show-more {{ display: block; width: 100%; padding: 12px; text-align: center; font-size: 13px; color: #787b86; background: transparent; border: none; cursor: pointer; }}
        .show-more:hover {{ color: #2962ff; }}
        .hidden-trades {{ display: none; }} .hidden-trades.show {{ display: block; }}
        .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e0e3eb; text-align: center; font-size: 12px; color: #787b86; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="strategy-badge">周五盘后打分|下周一收盘前10分钟买卖|1.5倍阈值</div>
            <div class="strategy-stats">回测: 16.36x | 47.50% | -26.65%回撤 | 夏普1.57 | 99次 | 54.1%</div>
            <div class="header-meta">评估日期: {trade_date} | 下次: {next_date}</div>
            <div class="header-time">数据更新: {update_time}</div>
        </div>
        <div class="section-title">当前持仓</div>
{holding_html}
        <div class="section-title">本周评分排名</div>
        <table class="rank-table">
            <thead>
                <tr><th style="width:50px">排名</th><th>ETF</th><th style="width:90px;text-align:right">周涨跌</th><th style="width:90px;text-align:right">得分</th></tr>
            </thead>
            <tbody>
{rank_table_body}
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
        <div class="trade-list">
{trade_list_html}
        </div>
        <div class="footer">ETF轮动策略 · 三因子动量模型 · 周五排名/周一收盘执行</div>
    </div>
    <script>
        function toggleTrades(btn) {{
            const content = document.getElementById('more-trades');
            content.classList.toggle('show');
            btn.textContent = content.classList.contains('show') ? '收起记录 ▲' : '查看更多记录 ▼';
        }}
    </script>
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
        df = get_etf_data(symbol, start_date, end_date)
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

    # 读取交易记录并计算持仓
    print("\n读取交易记录...")
    trades = parse_trade_records()
    position = calc_position(trades, etf_data)

    if position:
        print(f"  当前持仓: {position['name']} ({position['code']})")
        print(f"  持仓数量: {position['quantity']:,}股")
        print(f"  成本价: {position['cost']:.4f}")
        print(f"  当前价: {position['price']:.4f}")
        pnl_str = f"+{position['pnl_pct']:.2f}%" if position['pnl_pct'] >= 0 else f"{position['pnl_pct']:.2f}%"
        print(f"  持仓收益: {pnl_str}")
    else:
        print("  当前空仓")
        print(f"  ETF数据键: {list(etf_data.keys())}")
        if '159949.SZ' in etf_data:
            df = etf_data['159949.SZ']
            print(f"  159949数据:\n{df[['close']].tail(3)}")
            print(f"  最新价: {df['close'].iloc[-1]}")

    # 生成HTML
    print("\n正在生成HTML页面...")
    html = generate_html(factors, end_date, next_date, update_time, position, trades)

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

"""
ETF三因子轮动策略 - 周度评估+1.5倍阈值+收盘价执行版
验证周频+1.5倍阈值，周五收盘价执行（与原文一致）
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
import warnings
warnings.filterwarnings('ignore')

# 尝试导入Tushare
try:
    import sys
    sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    TUSHARE_AVAILABLE = True
except:
    TUSHARE_AVAILABLE = False

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

# 因子参数
BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20

# 因子权重
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4

# 调仓阈值
SWITCH_THRESHOLD = 1.5

# 交易费率 (单边)
COMMISSION_RATE = 0.0003

# 回测参数
START_DATE = '2019-01-01'
END_DATE = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
INITIAL_CAPITAL = 100000


def get_etf_data_tushare(symbol, start_date, end_date):
    try:
        if symbol.startswith('159'):
            ts_code = f"{symbol}.SZ"
        else:
            ts_code = f"{symbol}.SH"
        df = pro.fund_daily(ts_code=ts_code,
                           start_date=start_date.replace('-', ''),
                           end_date=end_date.replace('-', ''))
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        df = df.rename(columns={
            'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'vol': 'volume'
        })
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        return None


def get_etf_data(symbol, start_date, end_date):
    if TUSHARE_AVAILABLE:
        df = get_etf_data_tushare(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return df
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


def calc_all_factors(etf_data_dict, trade_date):
    factors = {}
    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue
        df = etf_data_dict[symbol]
        df_hist = df[df.index <= trade_date]
        if len(df_hist) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue
        factors[symbol] = {
            'name': name,
            'bias': calc_bias_momentum(df_hist['close']),
            'slope': calc_slope_momentum(df_hist['close']),
            'efficiency': calc_efficiency_momentum(df_hist)
        }
    return factors


def zscore_normalize(factors):
    if len(factors) < 2:
        return factors
    bias_vals = [f['bias'] for f in factors.values()]
    slope_vals = [f['slope'] for f in factors.values()]
    eff_vals = [f['efficiency'] for f in factors.values()]

    def zscore(vals):
        mean = np.mean(vals)
        std = np.std(vals)
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
            WEIGHT_BIAS * bias_z[i] +
            WEIGHT_SLOPE * slope_z[i] +
            WEIGHT_EFFICIENCY * eff_z[i]
        )
    return factors


def select_best_etf_with_threshold(factors, current_holding):
    """选择最优ETF - 带1.5倍阈值（原文策略）"""
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best_symbol = sorted_etfs[0][0]
    best_score = sorted_etfs[0][1]['total_score']

    if current_holding is None or current_holding not in factors:
        return best_symbol, True

    if current_holding == best_symbol:
        return current_holding, False

    current_score = factors[current_holding]['total_score']

    # 1.5倍阈值判断（原文逻辑）
    if current_score <= 0:
        if best_score > 0:
            return best_symbol, True
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol, True
        return current_holding, False
    else:
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol, True
        return current_holding, False


def run_weekly_backtest_close_price():
    """运行周度回测（收盘价执行）"""
    print("=" * 60)
    print("ETF三因子轮动策略 - 周度评估+1.5倍阈值+收盘价执行")
    print("=" * 60)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"佣金费率: {COMMISSION_RATE*10000:.0f} bps")
    print(f"调仓规则: 周五收盘排名，1.5倍阈值，周五收盘价执行（原文方式）")

    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        print(f"\n  获取 {name} ({symbol})...")
        df = get_etf_data(symbol, data_start, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            print(f"    ✓ {len(df)}条记录")

    if len(etf_data) < 4:
        print(f"\n❌ 数据不足")
        return None, None

    common_dates = None
    for df in etf_data.values():
        dates = set(df.index)
        common_dates = dates if common_dates is None else common_dates.intersection(dates)

    all_fridays = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])
    print(f"\n共 {len(all_fridays)} 个评估周五")

    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    nav_history = []
    trade_log = []

    for friday in all_fridays:
        # 周五收盘后计算因子
        factors = calc_all_factors(etf_data, friday)
        if not factors:
            continue

        factors = zscore_normalize(factors)

        # 选择目标ETF（带1.5倍阈值）
        target, should_trade = select_best_etf_with_threshold(factors, holding)

        # 如果触发调仓，周五收盘价执行（与原文一致！）
        if should_trade and target != holding and target is not None:
            # 卖出当前持仓（周五收盘价）
            if holding and holding_shares > 0:
                sell_price = etf_data[holding].loc[friday, 'close']
                capital = holding_shares * sell_price * (1 - COMMISSION_RATE)
                trade_log.append({
                    'date': friday,
                    'action': '卖出',
                    'symbol': holding,
                    'name': ETF_POOL[holding],
                    'price': sell_price,
                    'capital': capital
                })

            # 买入新目标（周五收盘价）
            buy_price = etf_data[target].loc[friday, 'close']
            holding_shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital = capital - holding_shares * buy_price

            trade_log.append({
                'date': friday,
                'action': '买入',
                'symbol': target,
                'name': ETF_POOL[target],
                'price': buy_price,
                'shares': holding_shares,
                'capital': capital
            })

            holding = target
            current_score = factors[target]['total_score']
            print(f"  {friday.strftime('%Y-%m-%d')} 调仓 → {ETF_POOL[target]} (得分: {current_score:.3f})")

        # 记录周五收盘净值
        if holding and holding in etf_data:
            friday_close = etf_data[holding].loc[friday, 'close']
            total_value = capital + holding_shares * friday_close
        else:
            total_value = capital

        nav_history.append({
            'date': friday,
            'nav': total_value / INITIAL_CAPITAL,
            'value': total_value,
            'holding': holding
        })

    # 计算结果
    nav_df = pd.DataFrame(nav_history).set_index('date')
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual_return = ((final_nav ** (1/years)) - 1) * 100
    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 52 - 0.03) / (daily_returns.std() * np.sqrt(52))
    rolling_max = nav_df['nav'].cummax()
    max_drawdown = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    print("\n" + "=" * 60)
    print("周度+1.5倍阈值+收盘价执行 回测结果")
    print("=" * 60)
    print(f"期末净值: {final_nav:.2f}倍")
    print(f"年化收益率: {annual_return:.2f}%")
    print(f"夏普比率: {sharpe:.2f}")
    print(f"最大回撤: {max_drawdown:.2f}%")
    print(f"交易次数: {len([t for t in trade_log if t['action'] == '买入'])}")

    print("\n" + "=" * 60)
    print("与目标数据对比")
    print("=" * 60)
    print(f"目标净值: 17.20倍 | 实际: {final_nav:.2f}倍 | 差异: {final_nav-17.20:+.2f}")
    print(f"目标年化: 48.59% | 实际: {annual_return:.2f}% | 差异: {annual_return-48.59:+.2f}%")
    print(f"目标回撤: -25.35% | 实际: {max_drawdown:.2f}%")
    print(f"目标交易: 99次   | 实际: {len([t for t in trade_log if t['action'] == '买入'])}次")

    return nav_df, trade_log


if __name__ == '__main__':
    nav_df, trade_log = run_weekly_backtest_close_price()

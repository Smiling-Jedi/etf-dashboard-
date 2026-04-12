"""
ETF三因子动量轮动策略回测 - 周度评估版
每周五收盘后计算因子，周一开盘调仓
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import tushare as ts
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans', 'Arial Unicode MS']
matplotlib.rcParams['axes.unicode_minus'] = False
import warnings
warnings.filterwarnings('ignore')

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890.SH': '红利低波ETF',
    '159949.SZ': '创业板50ETF',
    '513100.SH': '纳指ETF',
    '518880.SH': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40
SWITCH_THRESHOLD = 1.5
COMMISSION_RATE = 0.0003
START_DATE = '2019-01-01'
END_DATE = '2026-03-26'
INITIAL_CAPITAL = 100000


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
        print(f"获取 {symbol} 失败: {e}")
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


def select_best_etf(factors, current_holding):
    if not factors:
        return None
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best_symbol = sorted_etfs[0][0]
    best_score = sorted_etfs[0][1]['total_score']
    if current_holding is None or current_holding not in factors:
        return best_symbol
    if current_holding == best_symbol:
        return current_holding
    current_score = factors[current_holding]['total_score']
    if current_score <= 0:
        if best_score > 0 or best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding
    else:
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding


def get_weekly_trade_dates(trade_dates):
    """获取每周的最后一个交易日"""
    df = pd.DataFrame({'date': trade_dates})
    df['year_week'] = df['date'].dt.strftime('%Y-%U')
    # 每周最后一个交易日
    weekly_dates = df.groupby('year_week')['date'].max().tolist()
    return sorted(weekly_dates)


def run_backtest_weekly():
    print("=" * 70)
    print("ETF三因子动量轮动策略回测 - 周度评估版")
    print("=" * 70)
    print(f"\n评估频率: 每周五收盘后（周一开盘执行）")

    # 获取数据
    print(f"\n{'='*70}")
    print("正在获取ETF数据...")
    print(f"{'='*70}")

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        print(f"  获取 {name} ({symbol})...")
        df = get_etf_data_tushare(symbol, START_DATE, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            print(f"    ✓ 共 {len(df)} 条记录")

    if len(etf_data) < 2:
        print("错误：数据获取不足")
        return

    # 获取共同交易日
    common_dates = None
    for df in etf_data.values():
        dates = set(df.index)
        common_dates = dates if common_dates is None else common_dates.intersection(dates)

    all_trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])

    # 获取每周的评估日（周五）
    weekly_dates = get_weekly_trade_dates(all_trade_dates)
    print(f"\n总交易日: {len(all_trade_dates)} 天")
    print(f"周度评估次数: {len(weekly_dates)} 次")

    # 回测变量
    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    buy_price = 0
    nav_history = []
    trade_log = []

    print(f"\n{'='*70}")
    print("开始回测...")
    print(f"{'='*70}")

    for i, date in enumerate(all_trade_dates):
        # 只在每周最后一个交易日评估
        is_weekly_eval = date in weekly_dates

        if is_weekly_eval:
            factors = calc_all_factors(etf_data, date)
            if factors:
                factors = zscore_normalize(factors)
                target = select_best_etf(factors, holding)

                # 执行调仓（次日生效，这里简化为当日收盘）
                if target != holding:
                    # 卖出
                    if holding and holding_shares > 0:
                        sell_price = etf_data[holding].loc[date, 'close']
                        sell_value = holding_shares * sell_price * (1 - COMMISSION_RATE)
                        pnl = (sell_price / buy_price - 1) * 100
                        trade_log.append({
                            'date': date, 'action': '卖出', 'symbol': holding,
                            'name': ETF_POOL[holding], 'price': sell_price,
                            'shares': holding_shares, 'amount': holding_shares * sell_price,
                            'capital_after': sell_value, 'pnl_pct': pnl
                        })
                        capital = sell_value

                    # 买入
                    if target and target in etf_data:
                        buy_price = etf_data[target].loc[date, 'close']
                        buy_amount = capital * (1 - COMMISSION_RATE)
                        holding_shares = int(buy_amount / buy_price)
                        capital = capital - holding_shares * buy_price
                        holding = target
                        trade_log.append({
                            'date': date, 'action': '买入', 'symbol': target,
                            'name': ETF_POOL[target], 'price': buy_price,
                            'shares': holding_shares, 'amount': holding_shares * buy_price,
                            'capital_after': capital, 'pnl_pct': np.nan
                        })

        # 每日计算净值（无论是否评估）
        if holding and holding in etf_data:
            current_price = etf_data[holding].loc[date, 'close']
            total_value = capital + holding_shares * current_price
        else:
            total_value = capital

        nav_history.append({
            'date': date, 'nav': total_value / INITIAL_CAPITAL,
            'value': total_value, 'holding': holding
        })

        if (i + 1) % 252 == 0:
            print(f"  进度: {(i+1)/len(all_trade_dates)*100:.1f}% ({date.strftime('%Y-%m-%d')})")

    # 转换为DataFrame
    nav_df = pd.DataFrame(nav_history)
    nav_df.set_index('date', inplace=True)
    trade_df = pd.DataFrame(trade_log)

    # 计算绩效指标
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    annual_return = ((final_nav ** (1/years)) - 1) * 100
    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 252 - 0.03) / (daily_returns.std() * np.sqrt(252))
    rolling_max = nav_df['nav'].cummax()
    drawdown = (nav_df['nav'] - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100
    volatility = daily_returns.std() * np.sqrt(252) * 100

    num_trades = len(trade_df[trade_df['action'] == '买入'])
    if not trade_df[trade_df['action'] == '卖出'].empty:
        sell_trades = trade_df[trade_df['action'] == '卖出']
        win_trades = len(sell_trades[sell_trades['pnl_pct'] > 0])
        win_rate = win_trades / len(sell_trades) * 100 if len(sell_trades) > 0 else 0
        avg_pnl = sell_trades['pnl_pct'].mean()
    else:
        win_rate = 0
        avg_pnl = 0

    # 年度收益
    nav_df['year'] = nav_df.index.year
    yearly_stats = []
    for year, group in nav_df.groupby('year'):
        start_nav = group['nav'].iloc[0]
        end_nav = group['nav'].iloc[-1]
        ret = (end_nav / start_nav - 1) * 100
        year_roll_max = group['nav'].cummax()
        year_dd = (group['nav'] - year_roll_max) / year_roll_max
        year_mdd = year_dd.min() * 100
        yearly_stats.append({'Year': year, 'Return': ret, 'MaxDrawdown': year_mdd})
    yearly_df = pd.DataFrame(yearly_stats)

    # 打印结果
    print(f"\n{'='*70}")
    print("【周度评估】回测结果")
    print(f"{'='*70}")
    print(f"\n期末净值:     {final_nav:.4f} 倍")
    print(f"年化收益率:   {annual_return:.2f}%")
    print(f"最大回撤:     {max_drawdown:.2f}%")
    print(f"夏普比率:     {sharpe:.2f}")
    print(f"换仓次数:     {num_trades} 次")
    print(f"平均持仓周期: {len(all_trade_dates) / max(num_trades, 1):.1f} 天")
    print(f"胜率:         {win_rate:.1f}%")
    print(f"\n年度收益表:")
    print(yearly_df.to_string(index=False))

    # 保存
    with pd.ExcelWriter('backtest_result_weekly.xlsx', engine='openpyxl') as writer:
        nav_df.to_excel(writer, sheet_name='净值历史')
        if not trade_df.empty:
            trade_df.to_excel(writer, sheet_name='交易记录', index=False)
        yearly_df.to_excel(writer, sheet_name='年度统计', index=False)

    print(f"\n数据已保存: backtest_result_weekly.xlsx")
    return final_nav, annual_return, max_drawdown, sharpe, num_trades, win_rate


if __name__ == '__main__':
    run_backtest_weekly()

"""
ETF轮动策略最终对比 - 简化版
支持Tushare和iFinD双数据源
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# 配置
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

BIAS_N, MOMENTUM_DAY, SLOPE_N = 20, 25, 20
WEIGHT_BIAS, WEIGHT_SLOPE, WEIGHT_EFFICIENCY = 0.3, 0.3, 0.4
SWITCH_THRESHOLD = 1.5
COMMISSION_RATE = 0.0003
INITIAL_CAPITAL = 100000
START_DATE, END_DATE = '2019-01-01', (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')

# 初始化数据源
print("="*80)
print("ETF轮动策略 - 双数据源交叉验证")
print("="*80)

pro = None
try:
    import sys
    sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("✓ Tushare 已配置")
except Exception as e:
    print(f"✗ Tushare: {e}")


def get_data(symbol, start_date, end_date):
    """获取ETF数据"""
    try:
        ts_code = f"{symbol}.SZ" if symbol.startswith('159') else f"{symbol}.SH"
        df = pro.fund_daily(ts_code=ts_code,
                           start_date=start_date.replace('-', ''),
                           end_date=end_date.replace('-', ''))
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')
        return df[['open', 'high', 'low', 'close', 'vol']].rename(columns={'vol': 'volume'})
    except:
        return None


def calc_factors(etf_data, trade_date):
    """计算三因子"""
    factors = {}
    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data:
            continue
        df = etf_data[symbol]
        df_hist = df[df.index <= trade_date]
        if len(df_hist) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue

        # 乖离动量
        close = df_hist['close']
        ma = close.rolling(window=BIAS_N, min_periods=1).mean()
        bias = close / ma
        bias_recent = bias.iloc[-MOMENTUM_DAY:]
        x = np.arange(MOMENTUM_DAY).reshape(-1, 1)
        y = (bias_recent / bias_recent.iloc[0]).values
        lr = LinearRegression().fit(x, y)
        bias_score = float(lr.coef_[0] * 10000)

        # 斜率动量
        prices = close.iloc[-SLOPE_N:]
        norm_p = prices / prices.iloc[0]
        x2 = np.arange(1, SLOPE_N + 1).reshape(-1, 1)
        lr2 = LinearRegression().fit(x2, norm_p.values)
        slope = lr2.coef_[0]
        r2 = lr2.score(x2, norm_p.values)
        slope_score = float(10000 * slope * r2)

        # 效率动量
        df_recent = df.iloc[-MOMENTUM_DAY:].copy()
        pivot = (df_recent['open'] + df_recent['high'] + df_recent['low'] + df_recent['close']) / 4.0
        momentum = 100 * np.log(pivot.iloc[-1] / pivot.iloc[0])
        log_pivot = np.log(pivot)
        direction = abs(log_pivot.iloc[-1] - log_pivot.iloc[0])
        volatility = log_pivot.diff().abs().sum()
        eff_score = float(momentum * (direction / volatility if volatility > 0 else 0))

        factors[symbol] = {'name': name, 'bias': bias_score, 'slope': slope_score, 'efficiency': eff_score}

    if len(factors) < 2:
        return factors

    # Z-Score标准化
    for key in ['bias', 'slope', 'efficiency']:
        vals = [f[key] for f in factors.values()]
        mean, std = np.mean(vals), np.std(vals)
        for symbol in factors:
            factors[symbol][key + '_z'] = (factors[symbol][key] - mean) / std if std > 0 else 0

    for symbol in factors:
        f = factors[symbol]
        f['total_score'] = WEIGHT_BIAS * f['bias_z'] + WEIGHT_SLOPE * f['slope_z'] + WEIGHT_EFFICIENCY * f['efficiency_z']

    return factors


def select_with_threshold(factors, current):
    """带1.5倍阈值"""
    if not factors:
        return None
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best, best_score = sorted_etfs[0][0], sorted_etfs[0][1]['total_score']

    if current is None or current not in factors or current == best:
        return best

    curr_score = factors[current]['total_score']
    if curr_score <= 0:
        return best if best_score > 0 or best_score > curr_score * SWITCH_THRESHOLD else current
    return best if best_score > curr_score * SWITCH_THRESHOLD else current


def select_no_threshold(factors, current):
    """无阈值"""
    if not factors:
        return None
    return sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)[0][0]


def run_strategy(etf_data, strategy_name, select_func, freq='weekly'):
    """运行策略"""
    common_dates = set.intersection(*[set(df.index) for df in etf_data.values()])

    if freq == 'weekly':
        dates = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])
    else:
        dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])

    capital, holding, shares = INITIAL_CAPITAL, None, 0
    buy_price, last_trade = 0, None
    nav_history, trades, holding_periods = [], [], []

    for date in dates:
        factors = calc_factors(etf_data, date)
        if not factors:
            continue

        target = select_func(factors, holding)

        if target != holding and target is not None:
            # 卖出
            if holding and shares > 0:
                sell_price = etf_data[holding].loc[date, 'close']
                capital = shares * sell_price * (1 - COMMISSION_RATE)
                pnl = (sell_price / buy_price - 1) * 100 if buy_price > 0 else 0
                trades.append({'date': date, 'action': 'SELL', 'symbol': holding, 'pnl': pnl})
                if last_trade:
                    holding_periods.append((date - last_trade).days)

            # 买入
            buy_price = etf_data[target].loc[date, 'close']
            shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital -= shares * buy_price
            holding = target
            last_trade = date
            trades.append({'date': date, 'action': 'BUY', 'symbol': target})

        # 记录净值
        if holding:
            total = capital + shares * etf_data[holding].loc[date, 'close']
        else:
            total = capital
        nav_history.append({'date': date, 'nav': total / INITIAL_CAPITAL})

    # 计算指标
    nav_df = pd.DataFrame(nav_history).set_index('date')
    if len(nav_df) < 2:
        return None

    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual = (final_nav ** (1/years) - 1) * 100
    returns = nav_df['nav'].pct_change().dropna()

    periods = 52 if freq == 'weekly' else 252
    sharpe = (returns.mean() * periods - 0.03) / (returns.std() * np.sqrt(periods))

    rolling_max = nav_df['nav'].cummax()
    max_dd = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    sell_trades = [t for t in trades if t['action'] == 'SELL']
    wins = [t for t in sell_trades if t.get('pnl', 0) > 0]
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0

    avg_period = np.mean(holding_periods) if holding_periods else 0

    return {
        'strategy': strategy_name,
        'final_nav': final_nav,
        'annual': annual,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'trades': len([t for t in trades if t['action'] == 'BUY']),
        'win_rate': win_rate,
        'avg_holding': avg_period
    }


def main():
    # 加载数据
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    etf_data = {}

    print(f"\n{'='*60}")
    print("加载ETF数据 (Tushare)")
    print(f"{'='*60}")

    for symbol, name in ETF_POOL.items():
        print(f"\n  {name} ({symbol})...")
        df = get_data(symbol, data_start, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            print(f"    ✓ {len(df)}条记录 ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            print(f"    ✗ 失败")

    if len(etf_data) < 4:
        print("\n❌ 数据不足")
        return

    # 运行各策略
    strategies = [
        ('日频+1.5倍阈值', select_with_threshold, 'daily'),
        ('日频+无阈值', select_no_threshold, 'daily'),
        ('周频+1.5倍阈值', select_with_threshold, 'weekly'),
        ('周频+无阈值', select_no_threshold, 'weekly'),
    ]

    results = []
    for name, func, freq in strategies:
        print(f"\n  运行: {name}...")
        result = run_strategy(etf_data, name, func, freq)
        if result:
            results.append(result)
            print(f"    ✓ 净值: {result['final_nav']:.2f}x, 年化: {result['annual']:.2f}%")

    # 输出对比表
    print("\n" + "="*80)
    print("策略回测结果对比表")
    print("="*80)
    print(f"{'策略':<20} {'期末净值':<10} {'年化收益':<12} {'最大回撤':<10} {'夏普比率':<10} {'交易次数':<8} {'胜率':<8} {'平均持仓':<10}")
    print("-"*80)
    for r in results:
        print(f"{r['strategy']:<20} {r['final_nav']:>8.2f}x  {r['annual']:>9.2f}%  {r['max_dd']:>8.2f}%  {r['sharpe']:>8.2f}  {r['trades']:>6}  {r['win_rate']:>6.1f}%  {r['avg_holding']:>8.1f}天")

    # 保存CSV
    df = pd.DataFrame(results)
    df.to_csv('strategy_comparison.csv', index=False, encoding='utf-8-sig')
    print(f"\n✓ 结果已保存: strategy_comparison.csv")

    return results


if __name__ == '__main__':
    results = main()

"""
策略3开盘版：1.5倍阈值 + 下周一开盘卖出/下周一开盘买入
与策略3（收盘版）对比，验证开盘执行vs收盘执行的差异
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============ 配置 ============
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

print("="*80)
print("策略3开盘版：1.5倍阈值 + 周一开盘卖出/周一开盘买入")
print("="*80)

# 数据源配置
try:
    import sys
    sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("✓ Tushare 已配置")
except:
    pro = None
    print("✗ Tushare 未配置")


def get_data_tushare(symbol, start_date, end_date):
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

        close = df_hist['close']

        # 乖离动量
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
    """带1.5倍阈值选股"""
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best, best_score = sorted_etfs[0][0], sorted_etfs[0][1]['total_score']

    if current is None or current not in factors:
        return best, True
    if current == best:
        return current, False

    curr_score = factors[current]['total_score']

    if curr_score <= 0:
        if best_score > 0:
            return best, True
        if best_score > curr_score * SWITCH_THRESHOLD:
            return best, True
        return current, False
    else:
        if best_score > curr_score * SWITCH_THRESHOLD:
            return best, True
        return current, False


def get_next_monday_open(etf_data, symbol, friday):
    """获取下周一的开盘数据"""
    if symbol not in etf_data:
        return None
    df = etf_data[symbol]
    future = df[df.index > friday]
    for date in future.index:
        if date.weekday() == 0:  # 周一
            return date, df.loc[date, 'open']
    return None, None


def run_strategy_monday_open():
    """运行策略：周五排名，下周一开盘执行"""
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    etf_data = {}

    print(f"\n{'='*60}")
    print("加载ETF数据")
    print(f"{'='*60}")

    for symbol, name in ETF_POOL.items():
        print(f"  获取 {name} ({symbol})...")
        df = get_data_tushare(symbol, data_start, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            print(f"    ✓ {len(df)}条 ({df.index[0].date()} ~ {df.index[-1].date()})")

    if len(etf_data) < 4:
        print("\n❌ 数据不足")
        return None

    common_dates = set.intersection(*[set(df.index) for df in etf_data.values()])
    fridays = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])
    print(f"\n共 {len(fridays)} 个评估周五")

    capital, holding, shares = INITIAL_CAPITAL, None, 0
    buy_price, last_trade = 0, None
    nav_history, trades, holding_periods = [], [], []

    for friday in fridays:
        # 周五收盘后计算因子和排名
        factors = calc_factors(etf_data, friday)
        if not factors:
            continue

        target, should_trade = select_with_threshold(factors, holding)

        if should_trade and target != holding and target is not None:
            # 下周一开盘卖出旧持仓
            if holding:
                monday_sell, sell_price = get_next_monday_open(etf_data, holding, friday)
                if monday_sell is None:
                    continue
                capital = shares * sell_price * (1 - COMMISSION_RATE)
                pnl = (sell_price / buy_price - 1) * 100 if buy_price > 0 else 0
                trades.append({'date': monday_sell, 'action': 'SELL', 'symbol': holding, 'price': sell_price, 'pnl': pnl})
                if last_trade:
                    holding_periods.append((monday_sell - last_trade).days)
            else:
                monday_sell = None

            # 下周一开盘买入新目标
            monday_buy, buy_price_exec = get_next_monday_open(etf_data, target, friday)
            if monday_buy is None:
                continue

            buy_price = buy_price_exec
            shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital -= shares * buy_price
            holding = target
            last_trade = monday_buy
            trades.append({'date': monday_buy, 'action': 'BUY', 'symbol': target, 'price': buy_price})

            print(f"  {friday.strftime('%Y-%m-%d')} 调仓 → {ETF_POOL[target]} (得分: {factors[target]['total_score']:.3f}), 周一开盘执行")

        # 记录周五净值（用收盘价）
        if holding and holding in etf_data:
            total = capital + shares * etf_data[holding].loc[friday, 'close']
        else:
            total = capital
        nav_history.append({'date': friday, 'nav': total / INITIAL_CAPITAL})

    # 计算指标
    nav_df = pd.DataFrame(nav_history).set_index('date')
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual = (final_nav ** (1/years) - 1) * 100
    returns = nav_df['nav'].pct_change().dropna()
    sharpe = (returns.mean() * 52 - 0.03) / (returns.std() * np.sqrt(52))
    rolling_max = nav_df['nav'].cummax()
    max_dd = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    sell_trades = [t for t in trades if t['action'] == 'SELL']
    wins = [t for t in sell_trades if t.get('pnl', 0) > 0]
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0
    avg_period = np.mean(holding_periods) if holding_periods else 0

    print("\n" + "="*60)
    print("策略3开盘版回测结果")
    print("="*60)
    print(f"策略说明：周五收盘排名 → 下周一开盘卖出/买入")
    print(f"调仓阈值：1.5倍")
    print(f"期末净值：{final_nav:.2f}x")
    print(f"年化收益：{annual:.2f}%")
    print(f"夏普比率：{sharpe:.2f}")
    print(f"最大回撤：{max_dd:.2f}%")
    print(f"交易次数：{len([t for t in trades if t['action'] == 'BUY'])}")
    print(f"胜率：{win_rate:.1f}%")
    print(f"平均持仓：{avg_period:.1f}天")

    print("\n" + "="*60)
    print("与策略3收盘版对比")
    print("="*60)
    print(f"收盘版净值：13.71x | 开盘版：{final_nav:.2f}x | 差距：{final_nav-13.71:+.2f}x")

    return {
        'strategy': '策略3开盘版：1.5倍阈值+周一开盘买卖',
        'final_nav': final_nav,
        'annual': annual,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'trades': len([t for t in trades if t['action'] == 'BUY']),
        'win_rate': win_rate,
        'avg_holding': avg_period
    }


if __name__ == '__main__':
    result = run_strategy_monday_open()

    # 保存结果
    if result:
        df = pd.DataFrame([result])
        df.to_csv('策略3_开盘版_结果.csv', index=False, encoding='utf-8-sig')
        print(f"\n✓ 结果已保存: 策略3_开盘版_结果.csv")

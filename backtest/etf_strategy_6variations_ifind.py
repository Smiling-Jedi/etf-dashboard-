"""
ETF轮动策略 - 6种执行方式对比
优先使用同花顺iFinD数据，次选Tushare

6种策略：
1. 周频+1.5倍阈值+周五收盘价买卖
2. 周频+1.5倍阈值+周五收盘价卖出/下周一开盘价买入
3. 周频+1.5倍阈值+下周一收盘价卖出/下周一收盘价买入
4. 周频+无阈值+周五收盘价买卖
5. 周频+无阈值+周五收盘价卖出/下周一开盘价买入
6. 周频+无阈值+下周一收盘价卖出/下周一收盘价买入
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
print("ETF轮动策略 - 6种执行方式对比（优先iFinD）")
print("="*80)

# 尝试iFinD
try:
    import sys
    sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
    from app.data_sources.ifind_source import iFinDSource
    ifind = iFinDSource()
    USE_IFIND = True
    print("✓ iFinD 已配置")
except Exception as e:
    USE_IFIND = False
    print(f"✗ iFinD: {e}")

# 尝试Tushare
try:
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("✓ Tushare 备用")
except:
    pro = None
    print("✗ Tushare 不可用")


def get_data_ifind(symbol, start_date, end_date):
    """使用iFinD获取ETF历史数据"""
    try:
        name = ETF_POOL[symbol]
        # 获取历史行情
        result = ifind.get_historical_price(f"{symbol}.{'SZ' if symbol.startswith('159') else 'SH'}", name, period="7年")
        if result and result.get('success'):
            data = result.get('data', {})
            if isinstance(data, dict) and 'prices' in data:
                df = pd.DataFrame(data['prices'])
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
                df = df[(df.index >= start_date) & (df.index <= end_date)]
                return df[['open', 'high', 'low', 'close', 'volume']]
        return None
    except Exception as e:
        return None


def get_data_tushare(symbol, start_date, end_date):
    """使用Tushare获取ETF数据"""
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


def get_data(symbol, start_date, end_date):
    """获取数据，优先iFinD"""
    if USE_IFIND:
        df = get_data_ifind(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return df, 'ifind'
    if pro:
        df = get_data_tushare(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return df, 'tushare'
    return None, None


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
        df_recent = df_hist.iloc[-MOMENTUM_DAY:].copy()
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
    """
    带1.5倍阈值选股
    返回: (目标ETF, 是否触发调仓)
    """
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best, best_score = sorted_etfs[0][0], sorted_etfs[0][1]['total_score']

    # 空仓或无持仓，直接买入第1名
    if current is None or current not in factors:
        return best, True

    # 当前持仓已是第1名，不调仓
    if current == best:
        return current, False

    # 1.5倍阈值判断
    curr_score = factors[current]['total_score']

    if curr_score <= 0:
        # 当前得分<=0，新第1名>0 或 新第1名>当前*1.5 才调仓
        if best_score > 0:
            return best, True
        if best_score > curr_score * SWITCH_THRESHOLD:
            return best, True
        return current, False
    else:
        # 当前得分>0，新第1名必须>当前*1.5才调仓
        if best_score > curr_score * SWITCH_THRESHOLD:
            return best, True
        return current, False


def select_no_threshold(factors, current):
    """
    无阈值选股（排名变化即调）
    返回: (目标ETF, 是否触发调仓)
    """
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best = sorted_etfs[0][0]

    # 空仓或无持仓，直接买入第1名
    if current is None or current not in factors:
        return best, True

    # 只要排名第1的不是当前持仓，就调仓（无阈值）
    if best != current:
        return best, True

    return current, False


def get_next_weekday_date(etf_data, symbol, current_date, weekday=0):
    """获取下一个指定星期几的日期（0=周一）"""
    if symbol not in etf_data:
        return None
    df = etf_data[symbol]
    future = df[df.index > current_date]
    for date in future.index:
        if date.weekday() == weekday:
            return date
    return None


def run_strategy(etf_data, strategy_name, select_func, sell_timing='friday_close', buy_timing='friday_close'):
    """
    运行策略
    sell_timing: 'friday_close'周五收盘, 'monday_close'下周一收盘
    buy_timing: 'friday_close'周五收盘, 'monday_open'下周一开盘, 'monday_close'下周一收盘
    """
    common_dates = set.intersection(*[set(df.index) for df in etf_data.values()])
    fridays = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])

    capital, holding, shares = INITIAL_CAPITAL, None, 0
    buy_price, last_trade = 0, None
    nav_history, trades, holding_periods = [], [], []

    for friday in fridays:
        # 周五收盘后计算因子
        factors = calc_factors(etf_data, friday)
        if not factors:
            continue

        target, should_trade = select_func(factors, holding)

        if should_trade and target != holding and target is not None:
            # 确定卖出日期和价格
            if sell_timing == 'friday_close':
                sell_date = friday
                sell_price = etf_data[holding].loc[friday, 'close'] if holding else 0
            else:  # monday_close
                sell_date = get_next_weekday_date(etf_data, holding if holding else target, friday, 0)
                if sell_date is None:
                    continue
                sell_price = etf_data[holding].loc[sell_date, 'close'] if holding else 0

            # 确定买入日期和价格
            if buy_timing == 'friday_close':
                buy_date = friday
                buy_price_exec = etf_data[target].loc[friday, 'close']
            elif buy_timing == 'monday_open':
                buy_date = get_next_weekday_date(etf_data, target, friday, 0)
                if buy_date is None:
                    continue
                buy_price_exec = etf_data[target].loc[buy_date, 'open']
            else:  # monday_close
                buy_date = get_next_weekday_date(etf_data, target, friday, 0)
                if buy_date is None:
                    continue
                buy_price_exec = etf_data[target].loc[buy_date, 'close']

            # 卖出
            if holding and shares > 0:
                capital = shares * sell_price * (1 - COMMISSION_RATE)
                pnl = (sell_price / buy_price - 1) * 100 if buy_price > 0 else 0
                trades.append({'date': sell_date, 'action': 'SELL', 'symbol': holding, 'pnl': pnl})
                if last_trade:
                    holding_periods.append((sell_date - last_trade).days)

            # 买入
            buy_price = buy_price_exec
            shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital -= shares * buy_price
            holding = target
            last_trade = buy_date
            trades.append({'date': buy_date, 'action': 'BUY', 'symbol': target})

        # 记录周五净值
        if holding:
            total = capital + shares * etf_data[holding].loc[friday, 'close']
        else:
            total = capital
        nav_history.append({'date': friday, 'nav': total / INITIAL_CAPITAL})

    # 计算指标
    nav_df = pd.DataFrame(nav_history).set_index('date')
    if len(nav_df) < 2:
        return None

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
    data_sources = {}

    print(f"\n{'='*60}")
    print("加载ETF数据")
    print(f"{'='*60}")

    for symbol, name in ETF_POOL.items():
        print(f"\n  {name} ({symbol})...")
        df, source = get_data(symbol, data_start, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            data_sources[symbol] = source
            print(f"    ✓ {source}: {len(df)}条 ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            print(f"    ✗ 失败")

    if len(etf_data) < 4:
        print("\n❌ 数据不足")
        return

    print(f"\n  数据源汇总: {set(data_sources.values())}")

    # 6种策略
    strategies = [
        ('策略1: 1.5倍阈值+周五收盘买卖', select_with_threshold, 'friday_close', 'friday_close'),
        ('策略2: 1.5倍阈值+周五收盘卖/周一开盘买', select_with_threshold, 'friday_close', 'monday_open'),
        ('策略3: 1.5倍阈值+周一收盘卖/周一收盘买', select_with_threshold, 'monday_close', 'monday_close'),
        ('策略4: 无阈值+周五收盘买卖', select_no_threshold, 'friday_close', 'friday_close'),
        ('策略5: 无阈值+周五收盘卖/周一开盘买', select_no_threshold, 'friday_close', 'monday_open'),
        ('策略6: 无阈值+周一收盘卖/周一收盘买', select_no_threshold, 'monday_close', 'monday_close'),
    ]

    results = []
    for name, func, sell_t, buy_t in strategies:
        print(f"\n  运行: {name}...")
        result = run_strategy(etf_data, name, func, sell_t, buy_t)
        if result:
            results.append(result)
            print(f"    ✓ 净值: {result['final_nav']:.2f}x, 年化: {result['annual']:.2f}%")

    # 输出对比表
    print("\n" + "="*100)
    print("策略回测结果对比表（优先iFinD数据源）")
    print("="*100)
    print(f"{'策略':<45} {'净值':<8} {'年化':<10} {'回撤':<10} {'夏普':<8} {'交易':<6} {'胜率':<8} {'持仓':<10}")
    print("-"*100)
    for r in results:
        print(f"{r['strategy']:<45} {r['final_nav']:>6.2f}x {r['annual']:>8.2f}% {r['max_dd']:>8.2f}% {r['sharpe']:>6.2f} {r['trades']:>4} {r['win_rate']:>6.1f}% {r['avg_holding']:>8.1f}天")

    # 保存
    df = pd.DataFrame(results)
    df.to_csv('strategy_6variations_ifind.csv', index=False, encoding='utf-8-sig')
    print(f"\n✓ 结果已保存: strategy_6variations_ifind.csv")

    return results


if __name__ == '__main__':
    results = main()

"""
ETF轮动策略 - 6种执行方式双数据源交叉验证
Tushare vs iFinD 对比
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
print("ETF轮动策略 - 6种执行方式双数据源交叉验证")
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
    print("✓ Tushare 已配置")
except:
    pro = None
    print("✗ Tushare 未配置")


def get_data_ifind(symbol, start_date, end_date):
    """使用iFinD获取ETF历史数据 - 使用股票行情接口"""
    try:
        name = ETF_POOL[symbol]
        # iFinD数据可能格式不同，这里尝试获取
        result = ifind.get_historical_price(f"{symbol}.{'SZ' if symbol.startswith('159') else 'SH'}", name, period="7年")
        if result and result.get('success'):
            data = result.get('data', {})
            # 检查数据格式
            if isinstance(data, dict) and 'prices' in data:
                df = pd.DataFrame(data['prices'])
            elif isinstance(data, list):
                df = pd.DataFrame(data)
            else:
                return None
            if not df.empty and 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
                df = df[(df.index >= start_date) & (df.index <= end_date)]
                # 确保有所需列
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col not in df.columns:
                        return None
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


def load_data_source(source_name):
    """加载指定数据源的所有ETF数据"""
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    etf_data = {}

    print(f"\n{'='*60}")
    print(f"加载数据源: {source_name}")
    print(f"{'='*60}")

    for symbol, name in ETF_POOL.items():
        print(f"\n  获取 {name} ({symbol})...")
        if source_name == 'ifind':
            df = get_data_ifind(symbol, data_start, END_DATE)
        else:
            df = get_data_tushare(symbol, data_start, END_DATE)

        if df is not None and not df.empty:
            etf_data[symbol] = df
            print(f"    ✓ {len(df)}条 ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            print(f"    ✗ 失败")

    return etf_data


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
    """带1.5倍阈值选股，返回(target, should_trade)"""
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best, best_score = sorted_etfs[0][0], sorted_etfs[0][1]['total_score']

    # 空仓，直接买入第1名
    if current is None or current not in factors:
        return best, True

    # 当前持仓已是第1名，不调仓
    if current == best:
        return current, False

    # 1.5倍阈值判断
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


def select_no_threshold(factors, current):
    """无阈值选股，返回(target, should_trade)"""
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best = sorted_etfs[0][0]

    # 空仓，直接买入第1名
    if current is None or current not in factors:
        return best, True

    # 只要排名第1的不是当前持仓，就调仓
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
    """运行策略回测"""
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
    all_results = []

    # 对每个数据源运行回测
    for source_name in ['tushare', 'ifind']:
        if source_name == 'ifind' and not USE_IFIND:
            print(f"\n⚠️ iFinD 未配置，跳过")
            continue
        if source_name == 'tushare' and pro is None:
            print(f"\n⚠️ Tushare 未配置，跳过")
            continue

        etf_data = load_data_source(source_name)

        if len(etf_data) < 4:
            print(f"\n❌ {source_name} 数据不足，跳过")
            continue

        # 6种策略
        strategies = [
            ('策略1: 1.5倍阈值+周五收盘买卖', select_with_threshold, 'friday_close', 'friday_close'),
            ('策略2: 1.5倍阈值+周五收盘卖/周一开盘买', select_with_threshold, 'friday_close', 'monday_open'),
            ('策略3: 1.5倍阈值+周一收盘卖/周一收盘买', select_with_threshold, 'monday_close', 'monday_close'),
            ('策略4: 无阈值+周五收盘买卖', select_no_threshold, 'friday_close', 'friday_close'),
            ('策略5: 无阈值+周五收盘卖/周一开盘买', select_no_threshold, 'friday_close', 'monday_open'),
            ('策略6: 无阈值+周一收盘卖/周一收盘买', select_no_threshold, 'monday_close', 'monday_close'),
        ]

        for name, func, sell_t, buy_t in strategies:
            print(f"\n  运行: {name}...")
            result = run_strategy(etf_data, name, func, sell_t, buy_t)
            if result:
                result['data_source'] = source_name
                all_results.append(result)
                print(f"    ✓ 净值: {result['final_nav']:.2f}x, 年化: {result['annual']:.2f}%")

    # 输出双数据源对比表
    if all_results:
        print("\n" + "="*120)
        print("双数据源交叉验证对比表")
        print("="*120)
        print(f"{'策略':<40} {'数据源':<10} {'净值':<8} {'年化':<10} {'回撤':<10} {'夏普':<8} {'交易':<6} {'胜率':<8} {'持仓':<10}")
        print("-"*120)

        for r in sorted(all_results, key=lambda x: (x['strategy'], x['data_source'])):
            print(f"{r['strategy']:<40} {r['data_source']:<10} {r['final_nav']:>6.2f}x {r['annual']:>8.2f}% {r['max_dd']:>8.2f}% {r['sharpe']:>6.2f} {r['trades']:>4} {r['win_rate']:>6.1f}% {r['avg_holding']:>8.1f}天")

        # 保存CSV
        df = pd.DataFrame(all_results)
        df.to_csv('strategy_cross_validation.csv', index=False, encoding='utf-8-sig')
        print(f"\n✓ 结果已保存: strategy_cross_validation.csv")

        # 输出策略对比分析
        print("\n" + "="*120)
        print("策略排名（按年化收益率）")
        print("="*120)
        sorted_results = sorted(all_results, key=lambda x: x['annual'], reverse=True)
        for i, r in enumerate(sorted_results[:10], 1):
            print(f"{i}. {r['strategy']} ({r['data_source']}) - 年化: {r['annual']:.2f}%, 净值: {r['final_nav']:.2f}x")

    return all_results


if __name__ == '__main__':
    results = main()

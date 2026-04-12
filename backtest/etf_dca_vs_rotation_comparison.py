"""
ETF定投策略回测 - 每周定投1万元平均分配
对比：定投 vs 版本2轮动策略

定投规则：
1. 每周一开盘定投1万元
2. 平均分配到4只ETF，每只2500元
3. 不考虑择时，长期持有

对比对象：版本2轮动策略（双持仓50%+极端防守）
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

HS300_CODE = '000300.SH'

# 因子参数（版本2用）
BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40

# 交易费率
COMMISSION_RATE = 0.0003

# 回测参数
START_DATE = '2019-12-01'
END_DATE = '2026-03-26'

# 定投参数
WEEKLY_INVESTMENT = 10000  # 每周定投1万元
PER_ETF_INVESTMENT = 2500  # 每只ETF 2500元

# 极端防守阈值
EXTREME_DEFENSE_THRESHOLD = -0.05


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
        df = df.rename(columns={
            'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'vol': 'volume'
        })
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"获取 {symbol} 失败: {e}")
        return None


def get_index_data_tushare(symbol, start_date, end_date):
    """获取指数数据"""
    try:
        df = ts.pro_bar(ts_code=symbol, asset='I',
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


def get_top2_holdings(factors):
    if not factors or len(factors) < 2:
        return []
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    return [sorted_etfs[0][0], sorted_etfs[1][0]]


def check_extreme_defense(hs300_data, week_dates):
    if hs300_data is None or week_dates is None:
        return False
    for date in week_dates:
        if date not in hs300_data.index:
            continue
        try:
            day_data = hs300_data.loc[date]
            prev_close = hs300_data[hs300_data.index < date]['close'].iloc[-1] if len(hs300_data[hs300_data.index < date]) > 0 else day_data['close']
            daily_return = (day_data['close'] - prev_close) / prev_close
            if daily_return <= EXTREME_DEFENSE_THRESHOLD:
                return True
        except:
            continue
    return False


def run_comparison():
    """运行定投 vs 版本2对比回测"""
    print("=" * 80)
    print("ETF定投 vs 版本2轮动策略对比回测")
    print("=" * 80)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"定投规则: 每周一投入{WEEKLY_INVESTMENT/10000:.1f}万元，4只ETF各{PER_ETF_INVESTMENT/10000:.2f}万元")

    # 获取数据
    print(f"\n{'='*80}")
    print("正在获取ETF数据...")
    print(f"{'='*80}")

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        print(f"  获取 {name} ({symbol})...")
        df = get_etf_data_tushare(symbol, START_DATE, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            print(f"    ✓ 共 {len(df)} 条记录")

    if len(etf_data) < 4:
        print("错误：ETF数据获取不足")
        return

    # 获取沪深300数据
    print(f"\n  获取沪深300 ({HS300_CODE})...")
    hs300_data = get_index_data_tushare(HS300_CODE, START_DATE, END_DATE)
    if hs300_data is not None:
        print(f"    ✓ 共 {len(hs300_data)} 条记录")

    # 获取共同交易日
    common_dates = None
    for df in etf_data.values():
        dates = set(df.index)
        common_dates = dates if common_dates is None else common_dates.intersection(dates)

    trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])
    print(f"\n共同交易日: {len(trade_dates)} 天")

    # 按周分组（周一为定投日）
    weeks = {}
    for date in trade_dates:
        days_since_monday = date.weekday()
        monday = date - timedelta(days=days_since_monday)
        if monday not in weeks:
            weeks[monday] = []
        weeks[monday].append(date)

    sorted_mondays = sorted([m for m in weeks.keys() if m in trade_dates])
    print(f"定投周数: {len(sorted_mondays)} 周")

    # ============ 策略1：定投策略 ============
    print(f"\n{'='*80}")
    print("开始回测【定投策略】...")
    print(f"{'='*80}")

    fixed_investment_shares = {sym: 0 for sym in ETF_POOL.keys()}  # 每只ETF的持仓
    fixed_investment_cost = {sym: 0 for sym in ETF_POOL.keys()}   # 每只ETF的成本
    fixed_investment_total_cost = 0
    fixed_nav_history = []
    fixed_trade_log = []

    for week_idx, monday in enumerate(sorted_mondays):
        # 周一开盘定投
        if monday in trade_dates:
            for symbol in ETF_POOL.keys():
                if symbol in etf_data and monday in etf_data[symbol].index:
                    buy_price = etf_data[symbol].loc[monday, 'open']
                    buy_amount = PER_ETF_INVESTMENT * (1 - COMMISSION_RATE)
                    shares = int(buy_amount / buy_price)

                    if shares > 0:
                        cost = shares * buy_price
                        fixed_investment_shares[symbol] += shares
                        fixed_investment_cost[symbol] += cost
                        fixed_investment_total_cost += PER_ETF_INVESTMENT

                        fixed_trade_log.append({
                            'date': monday,
                            'action': '定投买入',
                            'symbol': symbol,
                            'name': ETF_POOL[symbol],
                            'price': buy_price,
                            'shares': shares,
                            'amount': cost,
                            'cumulative_cost': fixed_investment_total_cost
                        })

        # 记录本周每日净值（用周五收盘价）
        week_dates = weeks[monday]
        for date in week_dates:
            total_value = 0
            for symbol, shares in fixed_investment_shares.items():
                if symbol in etf_data and date in etf_data[symbol].index:
                    price = etf_data[symbol].loc[date, 'close']
                    total_value += shares * price

            fixed_nav_history.append({
                'date': date,
                'value': total_value,
                'total_cost': fixed_investment_total_cost,
                'nav': total_value / fixed_investment_total_cost if fixed_investment_total_cost > 0 else 1
            })

        if (week_idx + 1) % 50 == 0:
            print(f"  定投进度: {(week_idx+1)/len(sorted_mondays)*100:.1f}%")

    # ============ 策略2：版本2轮动策略 ============
    print(f"\n{'='*80}")
    print("开始回测【版本2轮动策略】...")
    print(f"{'='*80}")

    # 模拟同样的资金投入方式（每周一投入1万）
    rotation_cash = 0
    rotation_holdings = {}  # 当前持仓
    rotation_total_invested = 0
    rotation_nav_history = []
    rotation_trade_log = []
    last_week_top2 = []
    extreme_defense_active = False

    for week_idx, monday in enumerate(sorted_mondays):
        week_dates = weeks[monday]
        friday = week_dates[-1]  # 本周最后一个交易日

        # 周一开盘：投入新资金 + 执行调仓
        rotation_cash += WEEKLY_INVESTMENT
        rotation_total_invested += WEEKLY_INVESTMENT

        # 获取周一开盘价
        exec_open_prices = {}
        for symbol in ETF_POOL.keys():
            if symbol in etf_data and monday in etf_data[symbol].index:
                exec_open_prices[symbol] = etf_data[symbol].loc[monday, 'open']

        if week_idx == 0:
            # 第一周：直接买入当前排名前2（因为没有历史）
            factors = calc_all_factors(etf_data, friday)
            if factors:
                factors = zscore_normalize(factors)
                target_top2 = get_top2_holdings(factors)
                for symbol in target_top2:
                    if symbol in exec_open_prices:
                        buy_price = exec_open_prices[symbol]
                        buy_amount = (rotation_cash / 2) * (1 - COMMISSION_RATE)
                        shares = int(buy_amount / buy_price)
                        if shares > 0:
                            rotation_holdings[symbol] = shares
                            rotation_cash -= shares * buy_price
                            rotation_trade_log.append({
                                'date': monday, 'action': '买入', 'symbol': symbol,
                                'name': ETF_POOL[symbol], 'price': buy_price, 'shares': shares,
                                'amount': shares * buy_price
                            })
                last_week_top2 = target_top2
        else:
            # 后续周：周五评估，周一执行
            # 检查极端防守
            extreme_defense_triggered = check_extreme_defense(hs300_data, week_dates)
            extreme_defense_active = extreme_defense_triggered

            # 周五计算因子确定目标持仓
            factors = calc_all_factors(etf_data, friday)
            if factors:
                factors = zscore_normalize(factors)
                if extreme_defense_active:
                    target_top2 = ['512890.SH', '518880.SH']  # 红利+黄金
                else:
                    target_top2 = get_top2_holdings(factors)

                # 判断是否需要调仓
                if set(target_top2) != set(last_week_top2):
                    exited = [s for s in last_week_top2 if s not in target_top2]
                    entered = [s for s in target_top2 if s not in last_week_top2]

                    # 卖出退出的
                    for symbol in exited:
                        if symbol in rotation_holdings and rotation_holdings[symbol] > 0:
                            sell_price = exec_open_prices.get(symbol)
                            if sell_price:
                                shares = rotation_holdings[symbol]
                                sell_value = shares * sell_price * (1 - COMMISSION_RATE)
                                rotation_cash += sell_value
                                del rotation_holdings[symbol]
                                rotation_trade_log.append({
                                    'date': monday, 'action': '卖出', 'symbol': symbol,
                                    'name': ETF_POOL[symbol], 'price': sell_price,
                                    'shares': shares, 'amount': shares * sell_price
                                })

                    # 重新平衡两只持仓各占50%（包括新资金投入）
                    total_available = rotation_cash
                    for symbol, shares in rotation_holdings.items():
                        if symbol in exec_open_prices:
                            total_available += shares * exec_open_prices[symbol]

                    target_value_per_etf = total_available / 2

                    # 先处理保持的持仓
                    for symbol in target_top2:
                        if symbol in rotation_holdings:
                            # 已有持仓，计算需要调整
                            current_value = rotation_holdings[symbol] * exec_open_prices.get(symbol, 0)
                            if current_value < target_value_per_etf:
                                # 需要加仓
                                add_value = target_value_per_etf - current_value
                                add_shares = int(add_value * (1 - COMMISSION_RATE) / exec_open_prices[symbol])
                                if add_shares > 0:
                                    cost = add_shares * exec_open_prices[symbol]
                                    rotation_holdings[symbol] += add_shares
                                    rotation_cash -= cost
                            # 如果超过了，暂时不动（简化处理）

                    # 买入新进入的
                    for symbol in entered:
                        if symbol in exec_open_prices:
                            buy_price = exec_open_prices[symbol]
                            buy_amount = target_value_per_etf * (1 - COMMISSION_RATE)
                            shares = int(buy_amount / buy_price)
                            if shares > 0:
                                cost = shares * buy_price
                                rotation_holdings[symbol] = shares
                                rotation_cash -= cost
                                rotation_trade_log.append({
                                    'date': monday, 'action': '买入', 'symbol': symbol,
                                    'name': ETF_POOL[symbol], 'price': buy_price,
                                    'shares': shares, 'amount': cost
                                })

                    # 重新平衡现金（如果有剩余）
                    if rotation_cash > 1000:
                        for symbol in target_top2:
                            if symbol in exec_open_prices and symbol in rotation_holdings:
                                add_shares = int(rotation_cash / 2 / exec_open_prices[symbol])
                                if add_shares > 0:
                                    cost = add_shares * exec_open_prices[symbol]
                                    rotation_holdings[symbol] += add_shares
                                    rotation_cash -= cost

                    last_week_top2 = target_top2.copy()
                else:
                    # 排名没变，只投入新资金平衡
                    if rotation_cash > 1000 and len(last_week_top2) == 2:
                        for symbol in last_week_top2:
                            if symbol in exec_open_prices and symbol in rotation_holdings:
                                add_shares = int(rotation_cash / 2 / exec_open_prices[symbol])
                                if add_shares > 0:
                                    cost = add_shares * exec_open_prices[symbol]
                                    rotation_holdings[symbol] += add_shares
                                    rotation_cash -= cost

        # 记录每日净值
        for date in week_dates:
            total_value = rotation_cash
            for symbol, shares in rotation_holdings.items():
                if symbol in etf_data and date in etf_data[symbol].index:
                    price = etf_data[symbol].loc[date, 'close']
                    total_value += shares * price

            rotation_nav_history.append({
                'date': date,
                'value': total_value,
                'total_invested': rotation_total_invested,
                'nav': total_value / rotation_total_invested if rotation_total_invested > 0 else 1,
                'holdings': ','.join(rotation_holdings.keys())
            })

        if (week_idx + 1) % 50 == 0:
            print(f"  轮动进度: {(week_idx+1)/len(sorted_mondays)*100:.1f}%")

    # ============ 转换为DataFrame ============
    fixed_df = pd.DataFrame(fixed_nav_history)
    fixed_df.set_index('date', inplace=True)

    rotation_df = pd.DataFrame(rotation_nav_history)
    rotation_df.set_index('date', inplace=True)

    # ============ 计算绩效指标 ============
    def calc_metrics(df, cost_col):
        final_value = df['value'].iloc[-1]
        total_cost = df[cost_col].iloc[-1]
        years = (df.index[-1] - df.index[0]).days / 365.25

        # 计算IRR（内部收益率）
        total_return = (final_value / total_cost - 1) * 100
        annual_return = ((final_value / total_cost) ** (1/years) - 1) * 100

        daily_returns = df['nav'].pct_change().dropna()
        volatility = daily_returns.std() * np.sqrt(252) * 100

        rolling_max = df['nav'].cummax()
        drawdown = (df['nav'] - rolling_max) / rolling_max
        max_drawdown = drawdown.min() * 100

        sharpe = (daily_returns.mean() * 252 - 0.03) / (daily_returns.std() * np.sqrt(252))

        return {
            'final_value': final_value,
            'total_cost': total_cost,
            'profit': final_value - total_cost,
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'volatility': volatility,
            'sharpe': sharpe
        }

    fixed_metrics = calc_metrics(fixed_df, 'total_cost')
    rotation_metrics = calc_metrics(rotation_df, 'total_invested')

    # ============ 打印结果 ============
    print(f"\n{'='*80}")
    print("回测结果对比")
    print(f"{'='*80}")

    print(f"\n{'='*40}")
    print("【定投策略】")
    print(f"{'='*40}")
    print(f"累计投入:     {fixed_metrics['total_cost']/10000:.2f} 万元")
    print(f"期末资产:     {fixed_metrics['final_value']/10000:.2f} 万元")
    print(f"总盈亏:       {fixed_metrics['profit']/10000:+.2f} 万元 ({fixed_metrics['total_return']:+.2f}%)")
    print(f"年化收益率:   {fixed_metrics['annual_return']:.2f}%")
    print(f"最大回撤:     {fixed_metrics['max_drawdown']:.2f}%")
    print(f"夏普比率:     {fixed_metrics['sharpe']:.2f}")

    print(f"\n{'='*40}")
    print("【版本2轮动策略】")
    print(f"{'='*40}")
    print(f"累计投入:     {rotation_metrics['total_cost']/10000:.2f} 万元")
    print(f"期末资产:     {rotation_metrics['final_value']/10000:.2f} 万元")
    print(f"总盈亏:       {rotation_metrics['profit']/10000:+.2f} 万元 ({rotation_metrics['total_return']:+.2f}%)")
    print(f"年化收益率:   {rotation_metrics['annual_return']:.2f}%")
    print(f"最大回撤:     {rotation_metrics['max_drawdown']:.2f}%")
    print(f"夏普比率:     {rotation_metrics['sharpe']:.2f}")

    print(f"\n{'='*80}")
    print("策略对比")
    print(f"{'='*80}")
    print(f"{'指标':<20} {'定投策略':<20} {'版本2轮动':<20} {'差异':<20}")
    print("-" * 80)

    diff_value = rotation_metrics['final_value'] - fixed_metrics['final_value']
    diff_return = rotation_metrics['total_return'] - fixed_metrics['total_return']
    diff_annual = rotation_metrics['annual_return'] - fixed_metrics['annual_return']

    print(f"{'期末资产(万元)':<20} {fixed_metrics['final_value']/10000:<20.2f} {rotation_metrics['final_value']/10000:<20.2f} {diff_value/10000:+.2f}")
    print(f"{'总收益率(%)':<20} {fixed_metrics['total_return']:<20.2f} {rotation_metrics['total_return']:<20.2f} {diff_return:+.2f}")
    print(f"{'年化收益(%)':<20} {fixed_metrics['annual_return']:<20.2f} {rotation_metrics['annual_return']:<20.2f} {diff_annual:+.2f}")
    print(f"{'最大回撤(%)':<20} {fixed_metrics['max_drawdown']:<20.2f} {rotation_metrics['max_drawdown']:<20.2f} {rotation_metrics['max_drawdown'] - fixed_metrics['max_drawdown']:+.2f}")
    print(f"{'夏普比率':<20} {fixed_metrics['sharpe']:<20.2f} {rotation_metrics['sharpe']:<20.2f} {rotation_metrics['sharpe'] - fixed_metrics['sharpe']:+.2f}")

    # ============ 年度统计 ============
    fixed_df['year'] = fixed_df.index.year
    rotation_df['year'] = rotation_df.index.year

    print(f"\n{'='*80}")
    print("年度收益对比")
    print(f"{'='*80}")
    print(f"{'年份':<10} {'定投收益':<15} {'轮动收益':<15} {'定投资产':<15} {'轮动资产':<15}")
    print("-" * 80)

    for year in sorted(set(fixed_df['year'])):
        fixed_year = fixed_df[fixed_df['year'] == year]
        rotation_year = rotation_df[rotation_df['year'] == year]

        fixed_ret = (fixed_year['nav'].iloc[-1] / fixed_year['nav'].iloc[0] - 1) * 100
        rotation_ret = (rotation_year['nav'].iloc[-1] / rotation_year['nav'].iloc[0] - 1) * 100

        print(f"{year:<10} {fixed_ret:<15.2f} {rotation_ret:<15.2f} {fixed_year['value'].iloc[-1]/10000:<15.2f} {rotation_year['value'].iloc[-1]/10000:<15.2f}")

    # ============ 绘制图表 ============
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    # 图1: 资产价值对比
    ax1 = axes[0]
    ax1.plot(fixed_df.index, fixed_df['value']/10000,
             label=f'定投策略 (期末{fixed_metrics["final_value"]/10000:.2f}万)',
             color='#1f77b4', linewidth=2)
    ax1.plot(rotation_df.index, rotation_df['value']/10000,
             label=f'版本2轮动 (期末{rotation_metrics["final_value"]/10000:.2f}万)',
             color='#d62728', linewidth=2)
    ax1.set_title('累计资产价值对比', fontsize=14, fontweight='bold')
    ax1.set_ylabel('资产价值 (万元)')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')

    # 图2: 净值对比（投入成本归一化）
    ax2 = axes[1]
    ax2.plot(fixed_df.index, fixed_df['nav'],
             label=f'定投净值 ({fixed_metrics["total_return"]:.1f}%)',
             color='#1f77b4', linewidth=2)
    ax2.plot(rotation_df.index, rotation_df['nav'],
             label=f'轮动净值 ({rotation_metrics["total_return"]:.1f}%)',
             color='#d62728', linewidth=2)
    ax2.axhline(y=1, color='gray', linestyle='--', alpha=0.5, label='成本线')
    ax2.set_title('净值对比（以累计投入为基准）', fontsize=14)
    ax2.set_ylabel('净值')
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc='upper left')

    # 图3: 回撤对比
    ax3 = axes[2]
    fixed_rolling_max = fixed_df['nav'].cummax()
    fixed_drawdown = (fixed_df['nav'] - fixed_rolling_max) / fixed_rolling_max * 100

    rotation_rolling_max = rotation_df['nav'].cummax()
    rotation_drawdown = (rotation_df['nav'] - rotation_rolling_max) / rotation_rolling_max * 100

    ax3.fill_between(fixed_df.index, fixed_drawdown, 0, alpha=0.5, color='#1f77b4', label='定投回撤')
    ax3.fill_between(rotation_df.index, rotation_drawdown, 0, alpha=0.5, color='#d62728', label='轮动回撤')
    ax3.set_title('回撤对比', fontsize=14)
    ax3.set_ylabel('回撤 (%)')
    ax3.set_xlabel('日期')
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='lower left')

    plt.tight_layout()
    plt.savefig('backtest_comparison_dca_vs_rotation.png', dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: backtest_comparison_dca_vs_rotation.png")

    # ============ 保存Excel ============
    with pd.ExcelWriter('backtest_comparison_dca_vs_rotation.xlsx', engine='openpyxl') as writer:
        fixed_df.to_excel(writer, sheet_name='定投净值')
        rotation_df.to_excel(writer, sheet_name='轮动净值')
        pd.DataFrame(fixed_trade_log).to_excel(writer, sheet_name='定投记录', index=False)
        pd.DataFrame(rotation_trade_log).to_excel(writer, sheet_name='轮动记录', index=False)

        summary = pd.DataFrame({
            '指标': ['累计投入(万元)', '期末资产(万元)', '总盈亏(万元)', '总收益率(%)',
                    '年化收益率(%)', '最大回撤(%)', '夏普比率'],
            '定投策略': [fixed_metrics['total_cost']/10000, fixed_metrics['final_value']/10000,
                      fixed_metrics['profit']/10000, fixed_metrics['total_return'],
                      fixed_metrics['annual_return'], fixed_metrics['max_drawdown'],
                      fixed_metrics['sharpe']],
            '版本2轮动': [rotation_metrics['total_cost']/10000, rotation_metrics['final_value']/10000,
                       rotation_metrics['profit']/10000, rotation_metrics['total_return'],
                       rotation_metrics['annual_return'], rotation_metrics['max_drawdown'],
                       rotation_metrics['sharpe']]
        })
        summary.to_excel(writer, sheet_name='对比汇总', index=False)

    print(f"数据已保存: backtest_comparison_dca_vs_rotation.xlsx")
    print(f"\n{'='*80}")
    print("对比回测完成！")
    print(f"{'='*80}")

    return fixed_metrics, rotation_metrics


if __name__ == '__main__':
    fixed_metrics, rotation_metrics = run_comparison()

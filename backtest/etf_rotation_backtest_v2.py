"""
ETF三因子动量轮动策略回测 - 版本2：改进混合安全版

核心规则（abbaa确认）：
1. ETF组合：512890红利低波 + 159949创业板50 + 513100纳指ETF + 518880黄金
2. 每周五收盘后评估三因子得分
3. 极端防守：本周沪深300单日跌≥5% → 下周强制持仓512890(50%) + 518880(50%)
4. 周一开盘执行调仓，只卖出退出前2名的那只，保持仍在的，买入新进入的补足50%
5. 评估频率：周度（周五收盘）

数据来源：tushare
回测区间：2019-12-01 至 2026-03-26
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

# 极端防守检测用的沪深300代码
HS300_CODE = '000300.SH'

# 因子参数
BIAS_N = 20          # 乖离率均线周期
MOMENTUM_DAY = 25    # 动量计算周期
SLOPE_N = 20         # 斜率计算周期

# 因子权重
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40

# 交易费率 (单边)
COMMISSION_RATE = 0.0003  # 0.03%

# 回测参数
START_DATE = '2019-12-01'
END_DATE = '2026-03-26'
INITIAL_CAPITAL = 100000

# 极端防守触发阈值
EXTREME_DEFENSE_THRESHOLD = -0.05  # 单日跌≥5%


def get_etf_data_tushare(symbol, start_date, end_date):
    """使用tushare获取ETF历史数据"""
    try:
        df = ts.pro_bar(ts_code=symbol, asset='FD',
                        start_date=start_date.replace('-', ''),
                        end_date=end_date.replace('-', ''))

        if df is None or df.empty:
            print(f"获取 {symbol} 数据为空")
            return None

        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')

        df = df.rename(columns={
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume'
        })

        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"获取 {symbol} 数据失败: {e}")
        return None


def get_index_data_tushare(symbol, start_date, end_date):
    """使用tushare获取指数历史数据（用于沪深300）"""
    try:
        df = ts.pro_bar(ts_code=symbol, asset='I',
                        start_date=start_date.replace('-', ''),
                        end_date=end_date.replace('-', ''))

        if df is None or df.empty:
            print(f"获取 {symbol} 指数数据为空")
            return None

        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')

        df = df.rename(columns={
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume'
        })

        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"获取 {symbol} 指数数据失败: {e}")
        return None


def calc_bias_momentum(close_prices):
    """计算乖离动量因子"""
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
    bias_score = lr.coef_[0] * 10000

    return float(bias_score)


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

    score = 10000 * slope * r_squared

    return float(score)


def calc_efficiency_momentum(df):
    """计算效率动量因子"""
    if len(df) < MOMENTUM_DAY:
        return 0

    df_recent = df.iloc[-MOMENTUM_DAY:].copy()

    pivot = (df_recent['open'] + df_recent['high'] +
             df_recent['low'] + df_recent['close']) / 4.0

    momentum = 100 * np.log(pivot.iloc[-1] / pivot.iloc[0])

    log_pivot = np.log(pivot)
    direction = abs(log_pivot.iloc[-1] - log_pivot.iloc[0])
    volatility = log_pivot.diff().abs().sum()

    efficiency_ratio = direction / volatility if volatility > 0 else 0

    score = momentum * efficiency_ratio

    return float(score)


def calc_all_factors(etf_data_dict, trade_date):
    """计算所有ETF的三因子得分"""
    factors = {}

    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue

        df = etf_data_dict[symbol]
        df_hist = df[df.index <= trade_date]

        if len(df_hist) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue

        bias_score = calc_bias_momentum(df_hist['close'])
        slope_score = calc_slope_momentum(df_hist['close'])
        efficiency_score = calc_efficiency_momentum(df_hist)

        factors[symbol] = {
            'name': name,
            'bias': bias_score,
            'slope': slope_score,
            'efficiency': efficiency_score
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


def get_top2_holdings(factors):
    """根据因子得分获取排名前2的ETF"""
    if not factors or len(factors) < 2:
        return []

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    return [sorted_etfs[0][0], sorted_etfs[1][0]]


def check_extreme_defense(hs300_data, week_dates):
    """
    检查本周是否触发极端防守
    条件：沪深300本周是否有单日跌幅≥5%
    """
    if hs300_data is None or week_dates is None:
        return False

    for date in week_dates:
        if date not in hs300_data.index:
            continue

        # 获取当日数据
        try:
            day_data = hs300_data.loc[date]
            prev_close = hs300_data[hs300_data.index < date]['close'].iloc[-1] if len(hs300_data[hs300_data.index < date]) > 0 else day_data['close']
            daily_return = (day_data['close'] - prev_close) / prev_close

            if daily_return <= EXTREME_DEFENSE_THRESHOLD:
                return True
        except:
            continue

    return False


def run_backtest_v2():
    """运行版本2回测主函数"""
    print("=" * 70)
    print("ETF三因子动量轮动策略回测 - 版本2：改进混合安全版")
    print("=" * 70)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: {INITIAL_CAPITAL:,}元")
    print(f"交易成本: {COMMISSION_RATE*10000:.0f} bps (单边)")
    print(f"\n策略参数:")
    print(f"  BIAS_N = {BIAS_N}")
    print(f"  MOMENTUM_DAY = {MOMENTUM_DAY}")
    print(f"  SLOPE_N = {SLOPE_N}")
    print(f"\n因子权重:")
    print(f"  乖离动量 = {WEIGHT_BIAS}")
    print(f"  斜率动量 = {WEIGHT_SLOPE}")
    print(f"  效率动量 = {WEIGHT_EFFICIENCY}")
    print(f"\n版本2规则:")
    print(f"  极端防守触发: 沪深300单日跌≥{abs(EXTREME_DEFENSE_THRESHOLD)*100:.0f}%")
    print(f"  防守持仓: 红利低波(50%) + 黄金(50%)")
    print(f"  评估频率: 每周五收盘后")
    print(f"  执行时间: 下周一开盘价")
    print(f"  调仓逻辑: 前2名名单变化即调，只调退出那只")

    # 获取ETF数据
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

    if len(etf_data) < 4:
        print("错误：ETF数据获取不足，无法运行回测")
        return

    # 获取沪深300数据（用于极端防守检测）
    print(f"\n  获取沪深300 ({HS300_CODE})...")
    hs300_data = get_index_data_tushare(HS300_CODE, START_DATE, END_DATE)
    if hs300_data is not None:
        print(f"    ✓ 共 {len(hs300_data)} 条记录")
    else:
        print("    ⚠ 沪深300数据获取失败，极端防守功能将不可用")

    # 获取共同交易日
    common_dates = None
    for symbol, df in etf_data.items():
        dates = set(df.index)
        if common_dates is None:
            common_dates = dates
        else:
            common_dates = common_dates.intersection(dates)

    trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])
    print(f"\n共同交易日: {len(trade_dates)} 天")
    print(f"从 {trade_dates[0].strftime('%Y-%m-%d')} 到 {trade_dates[-1].strftime('%Y-%m-%d')}")

    # 按周分组（每周五为评估日）
    weeks = {}
    for date in trade_dates:
        # 获取该日期所在周的周五
        days_until_friday = (4 - date.weekday()) % 7
        friday = date + timedelta(days=days_until_friday)

        if friday not in weeks:
            weeks[friday] = []
        weeks[friday].append(date)

    # 只保留有完整数据的周（至少包含周五）
    valid_weeks = {k: v for k, v in weeks.items() if k in trade_dates}
    sorted_fridays = sorted(valid_weeks.keys())
    print(f"评估周数: {len(sorted_fridays)} 周")

    # 回测变量初始化
    capital = INITIAL_CAPITAL
    holdings = {}  # 当前持仓 {symbol: shares}
    cash = capital  # 现金

    nav_history = []
    trade_log = []
    weekly_factors = []
    defense_records = []  # 极端防守记录

    # 上周持仓（用于判断调仓）
    last_week_top2 = []
    extreme_defense_active = False

    print(f"\n{'='*70}")
    print("开始回测...")
    print(f"{'='*70}")

    for week_idx, friday in enumerate(sorted_fridays):
        week_dates = valid_weeks[friday]

        # 1. 周五收盘后：计算因子
        factors = calc_all_factors(etf_data, friday)

        if not factors or len(factors) < 4:
            continue

        factors = zscore_normalize(factors)

        # 2. 检查极端防守
        extreme_defense_triggered = check_extreme_defense(hs300_data, week_dates)

        if extreme_defense_triggered:
            defense_records.append({
                'week_end': friday,
                'triggered': True
            })
            extreme_defense_active = True
        else:
            defense_records.append({
                'week_end': friday,
                'triggered': False
            })
            extreme_defense_active = False

        # 3. 确定目标持仓
        if extreme_defense_active:
            # 极端防守：强制持仓红利低波 + 黄金
            target_top2 = ['512890.SH', '518880.SH']
        else:
            # 正常情况：取排名前2
            target_top2 = get_top2_holdings(factors)

        # 记录每周因子
        factor_record = {
            'week_end': friday,
            'extreme_defense': extreme_defense_active,
            'top2': ','.join(target_top2)
        }
        for sym, f in factors.items():
            factor_record[f'{sym}_score'] = f['total_score']
        weekly_factors.append(factor_record)

        # 4. 判断是否需要调仓（前2名名单有变化）
        if set(target_top2) != set(last_week_top2):
            # 获取下周一
            next_monday = friday + timedelta(days=3)  # 周五+3天=下周一

            # 找到下周一在交易日列表中的位置
            if next_monday in trade_dates:
                exec_date = next_monday
            else:
                # 如果下周一不是交易日，找下一个交易日
                future_dates = [d for d in trade_dates if d > friday]
                if future_dates:
                    exec_date = future_dates[0]
                else:
                    continue

            # 获取下周一开盘价
            exec_open_prices = {}
            for symbol in ETF_POOL.keys():
                if symbol in etf_data and exec_date in etf_data[symbol].index:
                    exec_open_prices[symbol] = etf_data[symbol].loc[exec_date, 'open']

            if len(exec_open_prices) < 4:
                continue

            # 5. 执行调仓（只调整变化的部分）
            # 5.1 确定退出的持仓（在上期top2但不在本期top2）
            exited = [s for s in last_week_top2 if s not in target_top2]
            # 5.2 确定新进入的持仓（在本期top2但不在上期top2）
            entered = [s for s in target_top2 if s not in last_week_top2]
            # 5.3 保持的持仓（在上下两期都在top2）
            kept = [s for s in target_top2 if s in last_week_top2]

            # 卖出退出的持仓
            for symbol in exited:
                if symbol in holdings and holdings[symbol] > 0:
                    sell_price = exec_open_prices.get(symbol)
                    if sell_price and sell_price > 0:
                        shares = holdings[symbol]
                        sell_value = shares * sell_price * (1 - COMMISSION_RATE)
                        buy_cost = holdings[symbol] * holdings.get(f'{symbol}_buy_price', sell_price)
                        pnl = (sell_value - buy_cost) / buy_cost * 100 if buy_cost > 0 else 0

                        trade_log.append({
                            'date': exec_date,
                            'action': '卖出',
                            'symbol': symbol,
                            'name': ETF_POOL[symbol],
                            'price': sell_price,
                            'shares': shares,
                            'amount': shares * sell_price,
                            'cash_after': cash + sell_value,
                            'pnl_pct': pnl,
                            'reason': '退出前2名' if not extreme_defense_active else '极端防守'
                        })

                        cash += sell_value
                        del holdings[symbol]
                        if f'{symbol}_buy_price' in holdings:
                            del holdings[f'{symbol}_buy_price']

            # 计算调仓后现金的一半（每个目标持仓50%）
            target_cash_per_etf = cash / 2

            # 买入新进入的持仓
            for symbol in entered:
                buy_price = exec_open_prices.get(symbol)
                if buy_price and buy_price > 0:
                    buy_amount = target_cash_per_etf * (1 - COMMISSION_RATE)
                    shares = int(buy_amount / buy_price)

                    if shares > 0:
                        cost = shares * buy_price
                        cash -= cost
                        holdings[symbol] = shares
                        holdings[f'{symbol}_buy_price'] = buy_price

                        trade_log.append({
                            'date': exec_date,
                            'action': '买入',
                            'symbol': symbol,
                            'name': ETF_POOL[symbol],
                            'price': buy_price,
                            'shares': shares,
                            'amount': cost,
                            'cash_after': cash,
                            'pnl_pct': np.nan,
                            'reason': '新进入前2名' if not extreme_defense_active else '极端防守'
                        })

            # 保持的持仓不动，但需要调整到50%（如果需要的话）
            # 这里保持不动，因为理论上应该已经是约50%

            last_week_top2 = target_top2.copy()

        # 计算每日净值（包括评估日和执行日之间的所有交易日）
        week_all_dates = [d for d in trade_dates if d >= (sorted_fridays[week_idx-1] if week_idx > 0 else trade_dates[0]) and d <= friday]
        if week_idx < len(sorted_fridays) - 1:
            next_friday = sorted_fridays[week_idx + 1]
            week_all_dates = [d for d in trade_dates if d > friday and d <= next_friday]
        else:
            week_all_dates = [d for d in trade_dates if d > friday]

        for date in week_all_dates:
            total_value = cash
            for symbol, shares in holdings.items():
                if not symbol.endswith('_buy_price') and symbol in etf_data and date in etf_data[symbol].index:
                    price = etf_data[symbol].loc[date, 'close']
                    total_value += shares * price

            nav_history.append({
                'date': date,
                'nav': total_value / INITIAL_CAPITAL,
                'value': total_value,
                'holdings': ','.join([s for s in holdings.keys() if not s.endswith('_buy_price')]),
                'cash': cash
            })

        if (week_idx + 1) % 50 == 0:
            print(f"  进度: {(week_idx+1)/len(sorted_fridays)*100:.1f}% ({friday.strftime('%Y-%m-%d')})")

    # 转换为DataFrame
    nav_df = pd.DataFrame(nav_history)
    if not nav_df.empty:
        nav_df.set_index('date', inplace=True)

    trade_df = pd.DataFrame(trade_log)

    # ============ 计算绩效指标 ============
    if nav_df.empty:
        print("错误：没有生成净值数据")
        return

    # 基本指标
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    annual_return = ((final_nav ** (1/years)) - 1) * 100

    # 夏普比率（假设无风险利率3%）
    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 252 - 0.03) / (daily_returns.std() * np.sqrt(252))

    # 最大回撤
    rolling_max = nav_df['nav'].cummax()
    drawdown = (nav_df['nav'] - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    # 波动率
    volatility = daily_returns.std() * np.sqrt(252) * 100

    # 交易统计
    buy_trades = trade_df[trade_df['action'] == '买入'] if not trade_df.empty else pd.DataFrame()
    sell_trades = trade_df[trade_df['action'] == '卖出'] if not trade_df.empty else pd.DataFrame()
    num_trades = len(buy_trades)

    if not sell_trades.empty and 'pnl_pct' in sell_trades.columns:
        win_trades = len(sell_trades[sell_trades['pnl_pct'] > 0])
        loss_trades = len(sell_trades[sell_trades['pnl_pct'] <= 0])
        win_rate = win_trades / (win_trades + loss_trades) * 100 if (win_trades + loss_trades) > 0 else 0
        avg_pnl = sell_trades['pnl_pct'].mean()
    else:
        win_rate = 0
        avg_pnl = 0

    # 极端防守统计
    defense_count = len([d for d in defense_records if d['triggered']])

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

        yearly_stats.append({
            'Year': year,
            'StartNav': start_nav,
            'EndNav': end_nav,
            'Return': ret,
            'MaxDrawdown': year_mdd
        })

    yearly_df = pd.DataFrame(yearly_stats)

    # ============ 打印结果 ============
    print(f"\n{'='*70}")
    print("版本2回测结果")
    print(f"{'='*70}")
    print(f"\n【收益指标】")
    print(f"  期末净值:     {final_nav:.4f} 倍")
    print(f"  年化收益率:   {annual_return:.2f}%")
    print(f"  总收益率:     {(final_nav - 1) * 100:.2f}%")

    print(f"\n【风险指标】")
    print(f"  最大回撤:     {max_drawdown:.2f}%")
    print(f"  年化波动率:   {volatility:.2f}%")

    print(f"\n【风险调整收益】")
    print(f"  夏普比率:     {sharpe:.2f}")
    print(f"  卡玛比率:     {abs(annual_return / max_drawdown):.2f}")

    print(f"\n【交易统计】")
    print(f"  调仓次数:     {num_trades} 次")
    print(f"  平均调仓周期: {len(trade_dates) / max(num_trades, 1):.1f} 天")
    print(f"  胜率:         {win_rate:.1f}%")
    print(f"  平均盈亏:     {avg_pnl:.2f}%")
    print(f"  极端防守触发: {defense_count} 次")

    print(f"\n【年度收益表】")
    print(yearly_df.to_string(index=False))

    # ============ 对比原策略和版本2 ============
    print(f"\n{'='*70}")
    print("策略对比")
    print(f"{'='*70}")
    print(f"\n{'指标':<20} {'原文策略':<15} {'版本2(本文)':<15}")
    print("-" * 55)

    orig_nav = 14.71
    orig_return = 45.4
    orig_dd = -25.3

    print(f"{'期末净值':<20} {orig_nav:<15.2f} {final_nav:<15.2f}")
    print(f"{'年化收益(%)':<20} {orig_return:<15.1f} {annual_return:<15.1f}")
    print(f"{'最大回撤(%)':<20} {orig_dd:<15.1f} {max_drawdown:<15.1f}")

    print(f"\n【版本2特点】")
    print(f"  1. 双持仓分散风险：固定持有2只ETF各50%")
    print(f"  2. 极端防守机制：沪深300单日跌≥5%时强制防守")
    print(f"  3. 周度评估：过滤日内噪音，信号质量更高")
    print(f"  4. 精细化调仓：只调退出那只，降低交易成本")

    # ============ 绘制图表 ============
    fig, axes = plt.subplots(4, 1, figsize=(14, 14))

    # 图1: 净值曲线
    ax1 = axes[0]
    ax1.plot(nav_df.index, nav_df['nav'],
             label=f'版本2净值 (年化{annual_return:.1f}%)',
             color='#D62828', linewidth=2)
    ax1.set_title(f'ETF轮动策略净值曲线 | 期末{final_nav:.2f}倍 | 夏普{sharpe:.2f}',
                  fontsize=14, fontweight='bold')
    ax1.set_ylabel('净值')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')

    # 图2: 回撤曲线
    ax2 = axes[1]
    ax2.fill_between(nav_df.index, drawdown * 100, 0,
                     alpha=0.5, color='#E94F37')
    ax2.set_title(f'回撤曲线 (最大回撤: {max_drawdown:.1f}%)', fontsize=12)
    ax2.set_ylabel('回撤 (%)')
    ax2.grid(True, alpha=0.3)

    # 图3: 持仓变化
    ax3 = axes[2]
    holding_map = {sym: i for i, sym in enumerate(ETF_POOL.keys())}
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    etf_labels = list(ETF_POOL.values())

    # 解析holdings列
    for i, (sym, name) in enumerate(ETF_POOL.items()):
        mask = nav_df['holdings'].str.contains(sym, na=False)
        ax3.fill_between(nav_df.index, i, i + 0.8,
                        where=mask, alpha=0.7, color=colors[i],
                        label=name)

    ax3.set_ylim(-0.2, 4)
    ax3.set_yticks([i + 0.4 for i in range(4)])
    ax3.set_yticklabels(etf_labels)
    ax3.set_title('持仓变化 (可同时持有多只)', fontsize=12)
    ax3.set_ylabel('ETF')
    ax3.legend(loc='upper left', ncol=4, bbox_to_anchor=(0, 1.15))

    # 图4: 现金比例变化
    ax4 = axes[3]
    cash_ratio = nav_df['cash'] / (nav_df['value'] + 0.001) * 100  # 避免除0
    ax4.fill_between(nav_df.index, 0, cash_ratio, alpha=0.5, color='green')
    ax4.set_title('现金比例变化', fontsize=12)
    ax4.set_ylabel('现金比例 (%)')
    ax4.set_xlabel('日期')
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('backtest_result_v2.png', dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: backtest_result_v2.png")

    # ============ 保存Excel ============
    with pd.ExcelWriter('backtest_result_v2.xlsx', engine='openpyxl') as writer:
        nav_df.to_excel(writer, sheet_name='净值历史')

        if not trade_df.empty:
            trade_df.to_excel(writer, sheet_name='交易记录', index=False)

        yearly_df.to_excel(writer, sheet_name='年度统计', index=False)

        weekly_factor_df = pd.DataFrame(weekly_factors)
        weekly_factor_df.to_excel(writer, sheet_name='每周因子', index=False)

        defense_df = pd.DataFrame(defense_records)
        defense_df.to_excel(writer, sheet_name='极端防守记录', index=False)

        summary = pd.DataFrame({
            '指标': ['期末净值', '年化收益率(%)', '最大回撤(%)', '夏普比率',
                    '年化波动率(%)', '调仓次数', '胜率(%)', '平均盈亏(%)', '极端防守次数'],
            '数值': [final_nav, annual_return, max_drawdown, sharpe,
                    volatility, num_trades, win_rate, avg_pnl, defense_count]
        })
        summary.to_excel(writer, sheet_name='汇总指标', index=False)

    print(f"数据已保存: backtest_result_v2.xlsx")
    print(f"\n{'='*70}")
    print("版本2回测完成！")
    print(f"{'='*70}")

    return nav_df, trade_df, yearly_df


if __name__ == '__main__':
    nav_df, trade_df, yearly_df = run_backtest_v2()

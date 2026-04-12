"""
ETF周内持有策略回测 - 周一买/周五卖/周末空仓

策略规则：
1. 每周五收盘计算三因子得分，确定下周目标持仓（前2名）
2. 下周一开盘买入目标ETF（各50%）
3. 本周五收盘卖出所有持仓
4. 周末空仓（现金状态）
5. 下周一重复流程

特点：只持有4个交易日，周末避险，降低黑天鹅风险
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

# 因子参数
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


def run_weekly_intraday_backtest():
    """运行周内持有策略回测"""
    print("=" * 80)
    print("ETF周内持有策略回测 - 周一买/周五卖/周末空仓")
    print("=" * 80)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: {INITIAL_CAPITAL:,}元")
    print(f"\n策略规则:")
    print(f"  1. 每周五收盘计算三因子得分")
    print(f"  2. 下周一开盘买入前2名ETF（各50%）")
    print(f"  3. 本周五收盘卖出所有持仓")
    print(f"  4. 周末空仓，现金状态")
    print(f"  5. 下周一重复流程")

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

    # 获取共同交易日
    common_dates = None
    for df in etf_data.values():
        dates = set(df.index)
        common_dates = dates if common_dates is None else common_dates.intersection(dates)

    trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])
    print(f"\n共同交易日: {len(trade_dates)} 天")

    # 按周分组
    weeks = {}
    for date in trade_dates:
        days_since_monday = date.weekday()
        monday = date - timedelta(days=days_since_monday)
        if monday not in weeks:
            weeks[monday] = []
        weeks[monday].append(date)

    sorted_mondays = sorted([m for m in weeks.keys() if m in trade_dates])
    print(f"完整周数: {len(sorted_mondays)} 周")

    # 回测变量
    capital = INITIAL_CAPITAL
    holdings = {}  # 当前持仓 {symbol: shares}
    cash = capital

    nav_history = []
    trade_log = []
    weekly_returns = []

    print(f"\n{'='*80}")
    print("开始回测...")
    print(f"{'='*80}")

    for week_idx, monday in enumerate(sorted_mondays):
        week_dates = weeks[monday]
        friday = week_dates[-1] if len(week_dates) >= 5 else week_dates[-1]

        # 1. 上周五计算因子，确定本周目标
        prev_friday = monday - timedelta(days=3)  # 上周5
        if prev_friday in trade_dates:
            eval_date = prev_friday
        else:
            # 找最近的有数据的周五
            past_dates = [d for d in trade_dates if d < monday]
            if past_dates:
                eval_date = past_dates[-1]
            else:
                eval_date = monday

        # 计算因子
        factors = calc_all_factors(etf_data, eval_date)
        if not factors or len(factors) < 4:
            continue

        factors = zscore_normalize(factors)
        target_top2 = get_top2_holdings(factors)

        # 2. 下周一开盘买入
        if monday in trade_dates:
            for symbol in target_top2:
                if symbol in etf_data and monday in etf_data[symbol].index:
                    buy_price = etf_data[symbol].loc[monday, 'open']
                    buy_amount = (cash / 2) * (1 - COMMISSION_RATE)
                    shares = int(buy_amount / buy_price)

                    if shares > 0:
                        cost = shares * buy_price
                        holdings[symbol] = shares
                        cash -= cost

                        trade_log.append({
                            'date': monday,
                            'action': '买入',
                            'symbol': symbol,
                            'name': ETF_POOL[symbol],
                            'price': buy_price,
                            'shares': shares,
                            'amount': cost,
                            'cash_after': cash
                        })

        # 3. 记录本周每日净值
        week_start_value = cash + sum(holdings.get(s, 0) * etf_data[s].loc[monday, 'close']
                                       for s in holdings if s in etf_data and monday in etf_data[s].index)

        for date in week_dates:
            total_value = cash
            for symbol, shares in holdings.items():
                if symbol in etf_data and date in etf_data[symbol].index:
                    price = etf_data[symbol].loc[date, 'close']
                    total_value += shares * price

            nav_history.append({
                'date': date,
                'nav': total_value / INITIAL_CAPITAL,
                'value': total_value,
                'holdings': ','.join(holdings.keys()),
                'cash': cash,
                'day_of_week': date.strftime('%A')
            })

        # 4. 本周五收盘卖出
        if friday in trade_dates and holdings:
            week_end_value = cash
            for symbol, shares in list(holdings.items()):
                if symbol in etf_data and friday in etf_data[symbol].index:
                    sell_price = etf_data[symbol].loc[friday, 'close']
                    sell_value = shares * sell_price * (1 - COMMISSION_RATE)
                    buy_price = trade_log[-1]['price'] if trade_log else sell_price
                    pnl = (sell_price / buy_price - 1) * 100

                    trade_log.append({
                        'date': friday,
                        'action': '卖出',
                        'symbol': symbol,
                        'name': ETF_POOL[symbol],
                        'price': sell_price,
                        'shares': shares,
                        'amount': shares * sell_price,
                        'cash_after': cash + sell_value,
                        'pnl_pct': pnl
                    })

                    week_end_value += sell_value

            # 计算本周收益
            week_return = (week_end_value / week_start_value - 1) * 100 if week_start_value > 0 else 0
            weekly_returns.append({
                'week': monday,
                'return': week_return,
                'start_value': week_start_value,
                'end_value': week_end_value
            })

            cash = week_end_value
            holdings = {}

        if (week_idx + 1) % 50 == 0:
            print(f"  进度: {(week_idx+1)/len(sorted_mondays)*100:.1f}%")

    # 转换为DataFrame
    nav_df = pd.DataFrame(nav_history)
    if not nav_df.empty:
        nav_df.set_index('date', inplace=True)

    trade_df = pd.DataFrame(trade_log)
    weekly_df = pd.DataFrame(weekly_returns)

    # ============ 计算绩效指标 ============
    if nav_df.empty:
        print("错误：没有生成净值数据")
        return

    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    annual_return = ((final_nav ** (1/years)) - 1) * 100

    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 252 - 0.03) / (daily_returns.std() * np.sqrt(252))

    rolling_max = nav_df['nav'].cummax()
    drawdown = (nav_df['nav'] - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    volatility = daily_returns.std() * np.sqrt(252) * 100

    # 交易统计
    num_trades = len(trade_df[trade_df['action'] == '买入'])
    if not trade_df[trade_df['action'] == '卖出'].empty:
        sell_trades = trade_df[trade_df['action'] == '卖出']
        win_trades = len(sell_trades[sell_trades['pnl_pct'] > 0])
        win_rate = win_trades / len(sell_trades) * 100 if len(sell_trades) > 0 else 0
        avg_pnl = sell_trades['pnl_pct'].mean()
    else:
        win_rate = 0
        avg_pnl = 0

    # 周末空仓时间占比
    weekend_days = len(nav_df[nav_df['day_of_week'].isin(['Saturday', 'Sunday'])])
    # 实际是周五收盘后卖出，周一开盘前买入，所以周末都是空仓

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

    # ============ 打印结果 ============
    print(f"\n{'='*80}")
    print("【周内持有策略】回测结果")
    print(f"{'='*80}")

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
    print(f"  交易周数:     {len(weekly_df)} 周")
    print(f"  买入次数:     {num_trades} 次")
    print(f"  平均周收益:   {weekly_df['return'].mean():.2f}%")
    print(f"  胜率:         {win_rate:.1f}%")
    print(f"  平均盈亏:     {avg_pnl:.2f}%")

    print(f"\n【年度收益表】")
    print(yearly_df.to_string(index=False))

    # 周收益分布
    print(f"\n【周收益分布】")
    print(f"  周收益>5%:    {len(weekly_df[weekly_df['return'] > 5])} 次 ({len(weekly_df[weekly_df['return'] > 5])/len(weekly_df)*100:.1f}%)")
    print(f"  周收益0-5%:   {len(weekly_df[(weekly_df['return'] > 0) & (weekly_df['return'] <= 5)])} 次")
    print(f"  周收益-5-0%:  {len(weekly_df[(weekly_df['return'] > -5) & (weekly_df['return'] <= 0)])} 次")
    print(f"  周收益<-5%:   {len(weekly_df[weekly_df['return'] <= -5])} 次 ({len(weekly_df[weekly_df['return'] <= -5])/len(weekly_df)*100:.1f}%)")

    # ============ 对比其他策略 ============
    print(f"\n{'='*80}")
    print("与其他策略对比")
    print(f"{'='*80}")
    print(f"\n{'指标':<20} {'周度持有':<15} {'周度轮动':<15} {'版本2':<15} {'原文':<15}")
    print("-" * 80)

    # 周度轮动数据（之前的回测结果）
    weekly_rotation = {'nav': 17.20, 'annual': 48.6, 'dd': -25.0, 'sharpe': 1.45}
    v2_data = {'nav': 3.14, 'annual': 20.27, 'dd': -13.90, 'sharpe': 1.19}
    original_data = {'nav': 14.71, 'annual': 45.4, 'dd': -25.3, 'sharpe': 1.40}

    print(f"{'期末净值':<20} {final_nav:<15.2f} {weekly_rotation['nav']:<15.2f} {v2_data['nav']:<15.2f} {original_data['nav']:<15.2f}")
    print(f"{'年化收益(%)':<20} {annual_return:<15.1f} {weekly_rotation['annual']:<15.1f} {v2_data['annual']:<15.1f} {original_data['annual']:<15.1f}")
    print(f"{'最大回撤(%)':<20} {max_drawdown:<15.1f} {weekly_rotation['dd']:<15.1f} {v2_data['dd']:<15.1f} {original_data['dd']:<15.1f}")
    print(f"{'夏普比率':<20} {sharpe:<15.2f} {weekly_rotation['sharpe']:<15.2f} {v2_data['sharpe']:<15.2f} {original_data['sharpe']:<15.2f}")

    print(f"\n【策略特点分析】")
    print(f"  周内持有优势:")
    print(f"    - 周末空仓，规避周末黑天鹅风险")
    print(f"    - 资金利用率高（只持有4天，周五收盘即空仓）")
    print(f"    - 交易频率高，每周调仓")
    print(f"  周内持有劣势:")
    print(f"    - 交易成本较高（每周一买一卖）")
    print(f"    - 可能错过周末重大利好消息")
    print(f"    - 频繁操作需要更多精力")

    # ============ 绘制图表 ============
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    # 图1: 净值曲线
    ax1 = axes[0]
    ax1.plot(nav_df.index, nav_df['nav'],
             label=f'周内持有策略 (年化{annual_return:.1f}%)',
             color='#D62828', linewidth=2)
    ax1.axhline(y=1, color='gray', linestyle='--', alpha=0.5)
    ax1.set_title(f'ETF周内持有策略净值曲线 | 期末{final_nav:.2f}倍 | 夏普{sharpe:.2f}',
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

    # 图3: 周收益分布
    ax3 = axes[2]
    if not weekly_df.empty:
        ax3.hist(weekly_df['return'], bins=30, alpha=0.7, color='steelblue', edgecolor='black')
        ax3.axvline(x=0, color='red', linestyle='--', alpha=0.7, label='零线')
        ax3.axvline(x=weekly_df['return'].mean(), color='green', linestyle='--', alpha=0.7, label=f'平均{weekly_df["return"].mean():.2f}%')
        ax3.set_title('周收益率分布', fontsize=12)
        ax3.set_xlabel('周收益率 (%)')
        ax3.set_ylabel('频次')
        ax3.legend()
        ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('backtest_result_weekly_intraday.png', dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: backtest_result_weekly_intraday.png")

    # ============ 保存Excel ============
    with pd.ExcelWriter('backtest_result_weekly_intraday.xlsx', engine='openpyxl') as writer:
        nav_df.to_excel(writer, sheet_name='净值历史')
        if not trade_df.empty:
            trade_df.to_excel(writer, sheet_name='交易记录', index=False)
        yearly_df.to_excel(writer, sheet_name='年度统计', index=False)
        weekly_df.to_excel(writer, sheet_name='周收益统计', index=False)

        summary = pd.DataFrame({
            '指标': ['期末净值', '年化收益率(%)', '最大回撤(%)', '夏普比率',
                    '年化波动率(%)', '交易周数', '胜率(%)', '平均周收益(%)'],
            '数值': [final_nav, annual_return, max_drawdown, sharpe,
                    volatility, len(weekly_df), win_rate, weekly_df['return'].mean() if not weekly_df.empty else 0]
        })
        summary.to_excel(writer, sheet_name='汇总指标', index=False)

    print(f"数据已保存: backtest_result_weekly_intraday.xlsx")
    print(f"\n{'='*80}")
    print("周内持有策略回测完成！")
    print(f"{'='*80}")

    return nav_df, trade_df, weekly_df


if __name__ == '__main__':
    nav_df, trade_df, weekly_df = run_weekly_intraday_backtest()

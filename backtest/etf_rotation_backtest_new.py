"""
ETF三因子动量轮动策略回测 - 新组合版本
基于原始策略修改：
1. ETF组合：512890红利低波 + 159967创成长 + 159941纳指100 + 518880黄金
2. 参数调整：MOMENTUM_DAY=20, WEIGHT_BIAS=0.25, WEIGHT_SLOPE=0.35
3. 数据来源：tushare
4. 回测区间：2019-12-01 至 2026-03-26
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
    '159967.SZ': '创成长ETF',
    '159941.SZ': '纳指100ETF',
    '518880.SH': '黄金ETF'
}

# 因子参数
BIAS_N = 20          # 乖离率均线周期
MOMENTUM_DAY = 25    # 【修正】动量计算周期：保持25，与原始策略一致
SLOPE_N = 20         # 斜率计算周期

# 因子权重
WEIGHT_BIAS = 0.30     # 【修正】乖离动量：恢复0.3
WEIGHT_SLOPE = 0.30    # 【修正】斜率动量：恢复0.3
WEIGHT_EFFICIENCY = 0.40  # 效率动量：保持0.4

# 调仓阈值
SWITCH_THRESHOLD = 1.5

# 交易费率 (单边)
COMMISSION_RATE = 0.0003  # 0.03%

# 回测参数
START_DATE = '2019-12-01'
END_DATE = '2026-03-26'
INITIAL_CAPITAL = 100000


def get_etf_data_tushare(symbol, start_date, end_date):
    """
    使用tushare获取ETF历史数据

    参数:
        symbol: ETF代码 (如 '512890.SH')
        start_date: 开始日期 (格式 '20191201')
        end_date: 结束日期 (格式 '20260326')
    返回:
        DataFrame with columns: open, high, low, close, volume
    """
    try:
        df = ts.pro_bar(ts_code=symbol, asset='FD',
                        start_date=start_date.replace('-', ''),
                        end_date=end_date.replace('-', ''))

        if df is None or df.empty:
            print(f"获取 {symbol} 数据为空")
            return None

        # 按日期排序
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.set_index('trade_date')

        # 统一列名
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


def calc_bias_momentum(close_prices):
    """
    计算乖离动量因子

    原理：衡量价格相对于均线的偏离程度及其变化趋势
    计算：对乖离率序列做线性回归，取斜率作为得分
    """
    if len(close_prices) < BIAS_N:
        return 0

    # 计算乖离度: price / ma
    ma = close_prices.rolling(window=BIAS_N, min_periods=1).mean()
    bias = close_prices / ma

    if len(bias) < MOMENTUM_DAY:
        return 0

    # 取最近MOMENTUM天的乖离率
    bias_recent = bias.iloc[-MOMENTUM_DAY:]
    x = np.arange(MOMENTUM_DAY).reshape(-1, 1)
    y = (bias_recent / bias_recent.iloc[0]).values

    # 线性回归求斜率
    lr = LinearRegression()
    lr.fit(x, y)
    bias_score = lr.coef_[0] * 10000  # 放大便于观察

    return float(bias_score)


def calc_slope_momentum(close_prices):
    """
    计算斜率动量因子

    原理：衡量价格上涨的速度与稳定性
    计算：对标准化价格做线性回归，斜率 × R²
    逻辑：涨得快且涨得稳的标的得分高
    """
    if len(close_prices) < SLOPE_N:
        return 0

    prices = close_prices.iloc[-SLOPE_N:]

    # 价格标准化（起点为1）
    normalized_prices = prices / prices.iloc[0]

    x = np.arange(1, SLOPE_N + 1).reshape(-1, 1)
    y = normalized_prices.values

    lr = LinearRegression()
    lr.fit(x, y)

    slope = lr.coef_[0]
    r_squared = lr.score(x, y)

    # 斜率 × R²：既看涨速又看稳定性
    score = 10000 * slope * r_squared

    return float(score)


def calc_efficiency_momentum(df):
    """
    计算效率动量因子

    原理：衡量价格上涨的"费力程度"
    计算：净涨幅 / 期间总波动幅度
    逻辑：以最小波动换取最大涨幅的"丝滑"走势得分高
    """
    if len(df) < MOMENTUM_DAY:
        return 0

    df_recent = df.iloc[-MOMENTUM_DAY:].copy()

    # 使用典型价格（OHLC平均）
    pivot = (df_recent['open'] + df_recent['high'] +
             df_recent['low'] + df_recent['close']) / 4.0

    # 动量（对数收益率）
    momentum = 100 * np.log(pivot.iloc[-1] / pivot.iloc[0])

    # 效率比计算
    log_pivot = np.log(pivot)
    direction = abs(log_pivot.iloc[-1] - log_pivot.iloc[0])  # 净位移
    volatility = log_pivot.diff().abs().sum()  # 总路程

    efficiency_ratio = direction / volatility if volatility > 0 else 0

    score = momentum * efficiency_ratio

    return float(score)


def calc_all_factors(etf_data_dict, trade_date):
    """
    计算所有ETF的三因子得分

    返回:
        dict: {symbol: {'name': ..., 'bias': ..., 'slope': ..., 'efficiency': ...}}
    """
    factors = {}

    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue

        df = etf_data_dict[symbol]
        df_hist = df[df.index <= trade_date]

        # 确保有足够的历史数据
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
    """
    Z-Score标准化

    将三个因子分别标准化后加权合成总分
    """
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


def select_best_etf(factors, current_holding):
    """
    选择最优ETF（带防抖动机制）

    只有当新第一名的得分超过当前持仓得分的1.5倍时才调仓
    """
    if not factors:
        return None

    # 按总分排序
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best_symbol = sorted_etfs[0][0]
    best_score = sorted_etfs[0][1]['total_score']

    # 当前无持仓，直接买入第一名
    if current_holding is None or current_holding not in factors:
        return best_symbol

    # 当前持仓就是第一名，不换
    if current_holding == best_symbol:
        return current_holding

    current_score = factors[current_holding]['total_score']

    # 调仓阈值判断
    if current_score <= 0:
        # 当前持仓得分为负，新标的为正即可换
        if best_score > 0:
            return best_symbol
        # 都为负，需要相对优势达1.5倍
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding
    else:
        # 当前持仓得分为正，需要1.5倍优势才换
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding


def run_backtest():
    """
    运行回测主函数
    """
    print("=" * 70)
    print("ETF三因子动量轮动策略回测 - 新组合版本")
    print("=" * 70)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: {INITIAL_CAPITAL:,}元")
    print(f"交易成本: {COMMISSION_RATE*10000:.0f} bps (单边)")
    print(f"\n策略参数:")
    print(f"  BIAS_N = {BIAS_N}")
    print(f"  MOMENTUM_DAY = {MOMENTUM_DAY}")
    print(f"  SLOPE_N = {SLOPE_N}")
    print(f"  SWITCH_THRESHOLD = {SWITCH_THRESHOLD}")
    print(f"\n因子权重:")
    print(f"  乖离动量 = {WEIGHT_BIAS}")
    print(f"  斜率动量 = {WEIGHT_SLOPE}")
    print(f"  效率动量 = {WEIGHT_EFFICIENCY}")

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
        print("错误：数据获取不足，无法运行回测")
        return

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

    # 回测变量初始化
    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    buy_price = 0

    nav_history = []  # 净值历史
    trade_log = []    # 交易记录
    daily_factors = []  # 每日因子记录

    print(f"\n{'='*70}")
    print("开始回测...")
    print(f"{'='*70}")

    for i, date in enumerate(trade_dates):
        # 计算因子
        factors = calc_all_factors(etf_data, date)

        if factors:
            factors = zscore_normalize(factors)
            target = select_best_etf(factors, holding)

            # 记录每日因子
            factor_record = {'date': date, 'holding': holding}
            for sym, f in factors.items():
                factor_record[f'{sym}_score'] = f['total_score']
            daily_factors.append(factor_record)

            # 执行调仓
            if target != holding:
                # 卖出当前持仓
                if holding and holding_shares > 0:
                    sell_price = etf_data[holding].loc[date, 'close']
                    sell_value = holding_shares * sell_price * (1 - COMMISSION_RATE)
                    pnl = (sell_price / buy_price - 1) * 100

                    trade_log.append({
                        'date': date,
                        'action': '卖出',
                        'symbol': holding,
                        'name': ETF_POOL[holding],
                        'price': sell_price,
                        'shares': holding_shares,
                        'amount': holding_shares * sell_price,
                        'capital_after': sell_value,
                        'pnl_pct': pnl
                    })

                    capital = sell_value

                # 买入新目标
                if target and target in etf_data:
                    buy_price = etf_data[target].loc[date, 'close']
                    buy_amount = capital * (1 - COMMISSION_RATE)
                    holding_shares = int(buy_amount / buy_price)
                    capital = capital - holding_shares * buy_price
                    holding = target

                    trade_log.append({
                        'date': date,
                        'action': '买入',
                        'symbol': target,
                        'name': ETF_POOL[target],
                        'price': buy_price,
                        'shares': holding_shares,
                        'amount': holding_shares * buy_price,
                        'capital_after': capital,
                        'pnl_pct': np.nan
                    })

        # 计算每日净值
        if holding and holding in etf_data:
            current_price = etf_data[holding].loc[date, 'close']
            total_value = capital + holding_shares * current_price
        else:
            total_value = capital

        nav_history.append({
            'date': date,
            'nav': total_value / INITIAL_CAPITAL,
            'value': total_value,
            'holding': holding
        })

        # 打印进度
        if (i + 1) % 252 == 0:
            print(f"  进度: {(i+1)/len(trade_dates)*100:.1f}% ({date.strftime('%Y-%m-%d')})")

    # 转换为DataFrame
    nav_df = pd.DataFrame(nav_history)
    nav_df.set_index('date', inplace=True)

    trade_df = pd.DataFrame(trade_log)

    # ============ 计算绩效指标 ============

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
    num_trades = len(trade_df[trade_df['action'] == '买入'])
    if not trade_df[trade_df['action'] == '卖出'].empty:
        sell_trades = trade_df[trade_df['action'] == '卖出']
        win_trades = len(sell_trades[sell_trades['pnl_pct'] > 0])
        loss_trades = len(sell_trades[sell_trades['pnl_pct'] <= 0])
        win_rate = win_trades / (win_trades + loss_trades) * 100 if (win_trades + loss_trades) > 0 else 0
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
    print("回测结果")
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
    print(f"  换仓次数:     {num_trades} 次")
    print(f"  平均持仓周期: {len(trade_dates) / max(num_trades, 1):.1f} 天")
    print(f"  胜率:         {win_rate:.1f}%")
    print(f"  平均盈亏:     {avg_pnl:.2f}%")

    print(f"\n【年度收益表】")
    print(yearly_df.to_string(index=False))

    # ============ 对比原策略 ============
    print(f"\n{'='*70}")
    print("与原策略对比")
    print(f"{'='*70}")
    print(f"\n{'指标':<20} {'原策略':<15} {'新组合':<15} {'差异':<15}")
    print("-" * 70)

    orig_nav = 14.71
    orig_return = 45.4
    orig_dd = -25.3

    print(f"{'期末净值':<20} {orig_nav:<15.2f} {final_nav:<15.2f} {final_nav - orig_nav:+.2f}")
    print(f"{'年化收益(%)':<20} {orig_return:<15.1f} {annual_return:<15.1f} {annual_return - orig_return:+.1f}")
    print(f"{'最大回撤(%)':<20} {orig_dd:<15.1f} {max_drawdown:<15.1f} {abs(max_drawdown) - abs(orig_dd):+.1f}")
    print(f"{'夏普比率':<20} {'1.40':<15} {sharpe:<15.2f} {sharpe - 1.40:+.2f}")

    print(f"\n【差异分析】")
    print(f"  1. ETF组合变化:")
    print(f"     原策略: 创业板50(159949) + 纳指ETF(513100)")
    print(f"     新组合: 创成长(159967) + 纳指100(159941)")
    print(f"  2. 创成长 vs 创业板50:")
    print(f"     - 创成长是Smart Beta策略，波动率更高({volatility:.1f}%)")
    print(f"     - 更适合动量捕捉，但回撤可能更大")
    print(f"  3. 纳指100 vs 纳指ETF:")
    print(f"     - 纳指100纯美股科技龙头")
    print(f"     - 与原纳指ETF差异较小，可视为同类替换")
    print(f"  4. 参数调整:")
    print(f"     - MOMENTUM_DAY: 25 -> 20 (更灵敏)")
    print(f"     - WEIGHT_SLOPE: 0.3 -> 0.35 (更重稳定性)")

    # ============ 绘制图表 ============
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    # 图1: 净值曲线
    ax1 = axes[0]
    ax1.plot(nav_df.index, nav_df['nav'],
             label=f'策略净值 (年化{annual_return:.1f}%)',
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
    holding_series = nav_df['holding'].map(holding_map)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    etf_labels = list(ETF_POOL.values())

    for i, (sym, name) in enumerate(ETF_POOL.items()):
        mask = nav_df['holding'] == sym
        ax3.fill_between(nav_df.index, 0, mask.astype(int),
                        where=mask, alpha=0.7, color=colors[i],
                        label=name, step='post')

    ax3.set_ylim(-0.1, 1.1)
    ax3.set_yticks([])
    ax3.set_title('持仓变化', fontsize=12)
    ax3.set_xlabel('日期')
    ax3.legend(loc='upper left', ncol=4)

    plt.tight_layout()
    plt.savefig('backtest_result_new.png', dpi=150, bbox_inches='tight')
    print(f"\n图表已保存: backtest_result_new.png")

    # ============ 保存Excel ============
    with pd.ExcelWriter('backtest_result_new.xlsx', engine='openpyxl') as writer:
        # 净值历史
        nav_df.to_excel(writer, sheet_name='净值历史')

        # 交易记录
        if not trade_df.empty:
            trade_df.to_excel(writer, sheet_name='交易记录', index=False)

        # 年度统计
        yearly_df.to_excel(writer, sheet_name='年度统计', index=False)

        # 汇总指标
        summary = pd.DataFrame({
            '指标': ['期末净值', '年化收益率(%)', '最大回撤(%)', '夏普比率',
                    '年化波动率(%)', '换仓次数', '胜率(%)', '平均盈亏(%)'],
            '数值': [final_nav, annual_return, max_drawdown, sharpe,
                    volatility, num_trades, win_rate, avg_pnl]
        })
        summary.to_excel(writer, sheet_name='汇总指标', index=False)

    print(f"数据已保存: backtest_result_new.xlsx")
    print(f"\n{'='*70}")
    print("回测完成！")
    print(f"{'='*70}")

    return nav_df, trade_df, yearly_df


if __name__ == '__main__':
    nav_df, trade_df, yearly_df = run_backtest()

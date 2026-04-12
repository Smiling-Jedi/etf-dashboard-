"""
ETF轮动策略完整对比分析
- 多策略对比（日频/周频、有阈值/无阈值、收盘价/开盘价执行）
- 双数据源交叉验证（Tushare vs iFinD）
- 输出完整指标：净值、年化收益、回撤、夏普、胜率、持仓周期
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

# ============ 配置 ============
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4
SWITCH_THRESHOLD = 1.5
COMMISSION_RATE = 0.0003
INITIAL_CAPITAL = 100000
START_DATE = '2019-01-01'
END_DATE = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')

# 数据源配置
DATA_SOURCES = {}

try:
    import sys
    sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    DATA_SOURCES['tushare'] = pro
    print("✓ Tushare 已配置")
except Exception as e:
    print(f"✗ Tushare 配置失败: {e}")

try:
    from app.data_sources.ifind_source import iFinDSource
    ifind = iFinDSource()
    DATA_SOURCES['ifind'] = ifind
    print("✓ iFinD 已配置")
except Exception as e:
    print(f"✗ iFinD 配置失败: {e}")


# ============ 数据获取 ============
def get_data_tushare(symbol, start_date, end_date):
    """使用Tushare获取ETF数据"""
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
    except:
        return None


def get_data_ifind(symbol, start_date, end_date):
    """使用iFinD获取ETF数据"""
    try:
        name = ETF_POOL[symbol]
        result = ifind.get_historical_price(symbol, name, period="7年")
        if result.get('success'):
            data = result.get('data', {})
            # 解析iFinD返回的数据
            df = pd.DataFrame(data)
            if not df.empty:
                df.index = pd.to_datetime(df.index)
                df = df[(df.index >= start_date) & (df.index <= end_date)]
                return df
        return None
    except:
        return None


def load_all_data(source_name, source_obj):
    """加载所有ETF数据"""
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    etf_data = {}

    print(f"\n{'='*60}")
    print(f"使用数据源: {source_name}")
    print(f"{'='*60}")

    for symbol, name in ETF_POOL.items():
        print(f"\n  获取 {name} ({symbol})...")
        if source_name == 'tushare':
            df = get_data_tushare(symbol, data_start, END_DATE)
        else:
            df = get_data_ifind(symbol, data_start, END_DATE)

        if df is not None and not df.empty:
            etf_data[symbol] = df
            print(f"    ✓ {len(df)}条记录 ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            print(f"    ✗ 获取失败")

    return etf_data


# ============ 因子计算 ============
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
        if symbol not in etf_data_dict:
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
            WEIGHT_BIAS * bias_z[i] + WEIGHT_SLOPE * slope_z[i] + WEIGHT_EFFICIENCY * eff_z[i]
        )
    return factors


# ============ 策略定义 ============
def select_etf_with_threshold(factors, current_holding):
    """策略A：带1.5倍阈值"""
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


def select_etf_no_threshold(factors, current_holding):
    """策略B：无阈值，排名变化即调"""
    if not factors:
        return None
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best_symbol = sorted_etfs[0][0]
    return best_symbol


# ============ 回测引擎 ============
def run_backtest(etf_data, strategy_name, select_func, frequency='weekly', execution='close'):
    """
    运行回测
    strategy_name: 策略名称
    select_func: 选股函数
    frequency: 'daily'日频 或 'weekly'周频
    execution: 'close'收盘价 或 'open'次日开盘价
    """
    if len(etf_data) < 4:
        return None

    common_dates = None
    for df in etf_data.values():
        dates = set(df.index)
        common_dates = dates if common_dates is None else common_dates.intersection(dates)

    # 根据频率筛选交易日
    if frequency == 'weekly':
        trade_dates = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])
    else:
        trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])

    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    buy_price = 0
    nav_history = []
    trades = []
    holding_periods = []
    last_trade_date = None

    for i, date in enumerate(trade_dates):
        factors = calc_all_factors(etf_data, date)
        if not factors:
            continue
        factors = zscore_normalize(factors)
        target = select_func(factors, holding)

        if target != holding and target is not None:
            # 确定执行价格
            if execution == 'close':
                # 当日收盘价执行
                exec_date = date
                sell_price = etf_data[holding].loc[date, 'close'] if holding else 0
                buy_price_exec = etf_data[target].loc[date, 'close']
            else:
                # 次日开盘价执行
                if i + 1 >= len(trade_dates):
                    break
                exec_date = trade_dates[i + 1]
                if frequency == 'weekly':
                    # 周频：下周一开盘
                    next_dates = [d for d in etf_data[holding].index if d > date] if holding else []
                    if not next_dates:
                        continue
                    exec_date = next_dates[0]
                    while exec_date.weekday() != 0:  # 找周一
                        idx = list(etf_data[holding].index).index(exec_date)
                        if idx + 1 >= len(etf_data[holding].index):
                            break
                        exec_date = etf_data[holding].index[idx + 1]

                sell_price = etf_data[holding].loc[date, 'close'] if holding else 0
                buy_price_exec = etf_data[target].loc[exec_date, 'open']

            # 记录持仓周期
            if holding and last_trade_date:
                holding_periods.append((exec_date - last_trade_date).days)

            # 卖出
            if holding and holding_shares > 0:
                capital = holding_shares * sell_price * (1 - COMMISSION_RATE)
                trades.append({
                    'date': date,
                    'action': 'SELL',
                    'symbol': holding,
                    'price': sell_price,
                    'pnl': (sell_price / buy_price - 1) * 100 if buy_price > 0 else 0
                })

            # 买入
            buy_price = buy_price_exec
            holding_shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital = capital - holding_shares * buy_price
            holding = target
            last_trade_date = exec_date

            trades.append({
                'date': exec_date if execution == 'open' else date,
                'action': 'BUY',
                'symbol': target,
                'price': buy_price
            })

        # 如果没有调仓，确保holding_periods不会为空
        if holding and last_trade_date is None:
            last_trade_date = date

        # 记录净值
        if holding and holding in etf_data:
            current_price = etf_data[holding].loc[date, 'close']
            total_value = capital + holding_shares * current_price
        else:
            total_value = capital

        nav_history.append({
            'date': date,
            'nav': total_value / INITIAL_CAPITAL,
            'holding': holding
        })

    # 计算指标
    nav_df = pd.DataFrame(nav_history).set_index('date')
    if len(nav_df) < 2:
        return None

    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual_return = ((final_nav ** (1/years)) - 1) * 100
    returns = nav_df['nav'].pct_change().dropna()

    # 夏普比率（年化）
    if frequency == 'weekly':
        sharpe = (returns.mean() * 52 - 0.03) / (returns.std() * np.sqrt(52))
    else:
        sharpe = (returns.mean() * 252 - 0.03) / (returns.std() * np.sqrt(252))

    # 最大回撤
    rolling_max = nav_df['nav'].cummax()
    max_drawdown = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    # 胜率
    buy_trades = [t for t in trades if t['action'] == 'BUY']
    sell_trades = [t for t in trades if t['action'] == 'SELL' and 'pnl' in t]
    win_rate = len([t for t in sell_trades if t['pnl'] > 0]) / len(sell_trades) * 100 if sell_trades else 0

    # 平均持仓周期
    avg_holding_period = np.mean(holding_periods) if holding_periods else 0

    return {
        'strategy': strategy_name,
        'frequency': frequency,
        'execution': execution,
        'final_nav': final_nav,
        'annual_return': annual_return,
        'max_drawdown': max_drawdown,
        'sharpe': sharpe,
        'trade_count': len(buy_trades),
        'win_rate': win_rate,
        'avg_holding_period': avg_holding_period,
        'nav_df': nav_df
    }


# ============ 主程序 ============
def main():
    print("=" * 80)
    print("ETF轮动策略完整对比分析")
    print("=" * 80)

    results = []

    # 对每个数据源运行回测
    for source_name, source_obj in DATA_SOURCES.items():
        etf_data = load_all_data(source_name, source_obj)
        if len(etf_data) < 4:
            print(f"\n⚠️ {source_name} 数据不足，跳过")
            continue

        strategies = [
            ('日频+1.5倍阈值+收盘价', select_etf_with_threshold, 'daily', 'close'),
            ('日频+无阈值+收盘价', select_etf_no_threshold, 'daily', 'close'),
            ('周频+1.5倍阈值+收盘价', select_etf_with_threshold, 'weekly', 'close'),
            ('周频+1.5倍阈值+开盘价', select_etf_with_threshold, 'weekly', 'open'),
            ('周频+无阈值+收盘价', select_etf_no_threshold, 'weekly', 'close'),
        ]

        for name, select_func, freq, exec_type in strategies:
            print(f"\n  运行: {name}...")
            result = run_backtest(etf_data, name, select_func, freq, exec_type)
            if result:
                result['data_source'] = source_name
                results.append(result)
                print(f"    ✓ 净值: {result['final_nav']:.2f}倍, 年化: {result['annual_return']:.2f}%")

    # 输出对比表
    print("\n" + "=" * 80)
    print("回测结果对比表")
    print("=" * 80)

    df_results = pd.DataFrame([{
        '数据源': r['data_source'],
        '策略': r['strategy'],
        '期末净值': f"{r['final_nav']:.2f}x",
        '年化收益': f"{r['annual_return']:.2f}%",
        '最大回撤': f"{r['max_drawdown']:.2f}%",
        '夏普比率': f"{r['sharpe']:.2f}",
        '交易次数': r['trade_count'],
        '胜率': f"{r['win_rate']:.1f}%",
        '平均持仓周期': f"{r['avg_holding_period']:.1f}天"
    } for r in results])

    print(df_results.to_string(index=False))

    # 保存详细结果
    df_results.to_csv('strategy_comparison_results.csv', index=False, encoding='utf-8-sig')
    print(f"\n✓ 结果已保存: strategy_comparison_results.csv")

    # 绘制对比图
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # 净值曲线对比
    ax1 = axes[0, 0]
    for r in results:
        label = f"{r['data_source']}-{r['strategy']}"
        ax1.plot(r['nav_df'].index, r['nav_df']['nav'], label=label, alpha=0.7)
    ax1.set_title('净值曲线对比', fontsize=14, fontweight='bold')
    ax1.set_ylabel('净值')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)

    # 收益率对比柱状图
    ax2 = axes[0, 1]
    strategies = [r['strategy'] for r in results if r['data_source'] == 'tushare']
    returns = [r['annual_return'] for r in results if r['data_source'] == 'tushare']
    ax2.bar(range(len(strategies)), returns)
    ax2.set_xticks(range(len(strategies)))
    ax2.set_xticklabels(strategies, rotation=45, ha='right', fontsize=8)
    ax2.set_title('年化收益率对比 (Tushare)', fontsize=14, fontweight='bold')
    ax2.set_ylabel('年化收益率 (%)')
    ax2.grid(True, alpha=0.3, axis='y')

    # 夏普比率对比
    ax3 = axes[1, 0]
    sharpes = [r['sharpe'] for r in results if r['data_source'] == 'tushare']
    ax3.bar(range(len(strategies)), sharpes, color='green', alpha=0.7)
    ax3.set_xticks(range(len(strategies)))
    ax3.set_xticklabels(strategies, rotation=45, ha='right', fontsize=8)
    ax3.set_title('夏普比率对比 (Tushare)', fontsize=14, fontweight='bold')
    ax3.set_ylabel('夏普比率')
    ax3.grid(True, alpha=0.3, axis='y')

    # 回撤对比
    ax4 = axes[1, 1]
    drawdowns = [abs(r['max_drawdown']) for r in results if r['data_source'] == 'tushare']
    ax4.bar(range(len(strategies)), drawdowns, color='red', alpha=0.7)
    ax4.set_xticks(range(len(strategies)))
    ax4.set_xticklabels(strategies, rotation=45, ha='right', fontsize=8)
    ax4.set_title('最大回撤对比 (Tushare)', fontsize=14, fontweight='bold')
    ax4.set_ylabel('最大回撤 (%)')
    ax4.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    plt.savefig('strategy_comparison_chart.png', dpi=150, bbox_inches='tight')
    print(f"✓ 图表已保存: strategy_comparison_chart.png")

    return results


if __name__ == '__main__':
    results = main()

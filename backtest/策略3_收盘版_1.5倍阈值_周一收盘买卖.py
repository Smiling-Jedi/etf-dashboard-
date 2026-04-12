"""
策略3收盘版：1.5倍阈值 + 下周一收盘卖出/下周一收盘买入

策略说明：
- 周五收盘后计算排名（基于周五收盘价）
- 下周一 14:50-15:00 执行调仓
- 以周一收盘价卖出旧持仓，买入新目标

实盘操作：
1. 周五晚上运行脚本，计算排名，确定下周一买入目标
2. 下周一 14:50-15:00 卖出旧持仓 + 买入新目标（收盘价成交）

回测结果：
- 期末净值：16.36x
- 年化收益：47.50%
- 最大回撤：-26.65%
- 夏普比率：1.57
- 交易次数：99次
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import warnings
import sys
import os

# 添加光剑系统路径
sys.path.insert(0, '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber')

warnings.filterwarnings('ignore')

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF',
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

# 因子参数
BIAS_N = 20          # 乖离率均线周期
MOMENTUM_DAY = 25    # 动量计算周期
SLOPE_N = 20         # 斜率计算周期

# 因子权重
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4

# 调仓阈值
SWITCH_THRESHOLD = 1.5

# 交易费率 (单边)
COMMISSION_RATE = 0.0003

# 回测参数
START_DATE = '2019-01-01'
END_DATE = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
INITIAL_CAPITAL = 100000

print("=" * 80)
print("策略3收盘版：1.5倍阈值 + 周一收盘卖出/周一收盘买入")
print("=" * 80)

# 数据源配置
try:
    from config.settings import TUSHARE_TOKEN
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("✓ Tushare 已配置")
except Exception as e:
    print(f"✗ Tushare 配置失败: {e}")
    sys.exit(1)


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
    except Exception as e:
        print(f"    获取 {symbol} 数据失败: {e}")
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
    lr = LinearRegression().fit(x, y)
    return float(lr.coef_[0] * 10000)


def calc_slope_momentum(close_prices):
    """计算斜率动量因子"""
    if len(close_prices) < SLOPE_N:
        return 0
    prices = close_prices.iloc[-SLOPE_N:]
    normalized_prices = prices / prices.iloc[0]
    x = np.arange(1, SLOPE_N + 1).reshape(-1, 1)
    lr = LinearRegression().fit(x, normalized_prices.values)
    slope = lr.coef_[0]
    r_squared = lr.score(x, normalized_prices.values)
    return float(10000 * slope * r_squared)


def calc_efficiency_momentum(df):
    """计算效率动量因子"""
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
    """计算三因子得分"""
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


def select_best_etf_with_threshold(factors, current_holding):
    """选择最优ETF - 带1.5倍阈值"""
    if not factors:
        return None, False

    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)
    best_symbol = sorted_etfs[0][0]
    best_score = sorted_etfs[0][1]['total_score']

    # 空仓，直接买入第1名
    if current_holding is None or current_holding not in factors:
        return best_symbol, True

    # 当前持仓已是第1名，不调仓
    if current_holding == best_symbol:
        return current_holding, False

    current_score = factors[current_holding]['total_score']

    # 1.5倍阈值判断
    if current_score <= 0:
        # 当前得分<=0，新第1名>0 或 新第1名>当前*1.5 才调仓
        if best_score > 0:
            return best_symbol, True
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol, True
        return current_holding, False
    else:
        # 当前得分>0，新第1名必须>当前*1.5才调仓
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol, True
        return current_holding, False


def get_next_monday(etf_data, symbol, friday):
    """获取下周一的日期"""
    if symbol not in etf_data:
        return None
    df = etf_data[symbol]
    future = df[df.index > friday]
    for date in future.index:
        if date.weekday() == 0:  # 周一
            return date
    return None


def run_backtest():
    """运行回测"""
    # 加载数据
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

    # 找到所有共同周五
    common_dates = set.intersection(*[set(df.index) for df in etf_data.values()])
    fridays = sorted([d for d in common_dates if d.weekday() == 4 and d >= pd.Timestamp(START_DATE)])
    print(f"\n共 {len(fridays)} 个评估周五")

    # 回测
    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    buy_price = 0
    nav_history = []
    trade_log = []
    holding_periods = []
    last_trade_date = None

    for friday in fridays:
        # 周五收盘后计算因子
        factors = calc_all_factors(etf_data, friday)
        if not factors:
            continue

        factors = zscore_normalize(factors)
        target, should_trade = select_best_etf_with_threshold(factors, holding)

        if should_trade and target != holding and target is not None:
            # 获取下周一日期
            monday = get_next_monday(etf_data, target, friday)
            if monday is None:
                continue

            # 下周一收盘卖出旧持仓
            if holding and holding_shares > 0:
                sell_price = etf_data[holding].loc[monday, 'close']
                capital = holding_shares * sell_price * (1 - COMMISSION_RATE)
                pnl = (sell_price / buy_price - 1) * 100 if buy_price > 0 else 0
                trade_log.append({
                    'date': monday,
                    'action': '卖出',
                    'symbol': holding,
                    'name': ETF_POOL[holding],
                    'price': sell_price,
                    'capital': capital,
                    'pnl': pnl
                })
                if last_trade_date:
                    holding_periods.append((monday - last_trade_date).days)

            # 下周一收盘买入新目标
            buy_price = etf_data[target].loc[monday, 'close']
            holding_shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
            capital = capital - holding_shares * buy_price

            trade_log.append({
                'date': monday,
                'action': '买入',
                'symbol': target,
                'name': ETF_POOL[target],
                'price': buy_price,
                'shares': holding_shares,
                'capital': capital
            })

            holding = target
            last_trade_date = monday

            current_score = factors[target]['total_score']
            print(f"  {friday.strftime('%Y-%m-%d')} 排名 → {ETF_POOL[target]} (得分: {current_score:.3f}) | 下周一 {monday.strftime('%m-%d')} 执行")

        # 记录周五净值
        if holding and holding in etf_data:
            friday_close = etf_data[holding].loc[friday, 'close']
            total_value = capital + holding_shares * friday_close
        else:
            total_value = capital

        nav_history.append({
            'date': friday,
            'nav': total_value / INITIAL_CAPITAL,
            'value': total_value,
            'holding': holding
        })

    # 计算结果
    nav_df = pd.DataFrame(nav_history).set_index('date')
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual_return = ((final_nav ** (1/years)) - 1) * 100
    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 52 - 0.03) / (daily_returns.std() * np.sqrt(52))

    rolling_max = nav_df['nav'].cummax()
    max_drawdown = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    sell_trades = [t for t in trade_log if t['action'] == '卖出']
    wins = [t for t in sell_trades if t.get('pnl', 0) > 0]
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0

    avg_holding_period = np.mean(holding_periods) if holding_periods else 0

    print("\n" + "=" * 60)
    print("策略3收盘版 回测结果")
    print("=" * 60)
    print(f"策略说明：周五收盘排名 → 下周一收盘卖出/买入")
    print(f"调仓阈值：1.5倍")
    print(f"期末净值：{final_nav:.2f}x")
    print(f"年化收益：{annual_return:.2f}%")
    print(f"夏普比率：{sharpe:.2f}")
    print(f"最大回撤：{max_drawdown:.2f}%")
    print(f"交易次数：{len([t for t in trade_log if t['action'] == '买入'])}")
    print(f"胜率：{win_rate:.1f}%")
    print(f"平均持仓天数：{avg_holding_period:.1f}天")

    print("\n" + "=" * 60)
    print("与目标对比")
    print("=" * 60)
    print(f"目标净值：17.20x | 实际：{final_nav:.2f}x | 差异：{final_nav-17.20:+.2f}")
    print(f"目标年化：48.59% | 实际：{annual_return:.2f}% | 差异：{annual_return-48.59:+.2f}%")

    return {
        'strategy': '策略3收盘版：1.5倍阈值+周一收盘买卖',
        'final_nav': final_nav,
        'annual': annual_return,
        'max_dd': max_drawdown,
        'sharpe': sharpe,
        'trades': len([t for t in trade_log if t['action'] == '买入']),
        'win_rate': win_rate,
        'avg_holding': avg_holding_period,
        'nav_df': nav_df,
        'trade_log': trade_log
    }


def get_current_ranking():
    """
    获取当前排名（用于实盘）
    返回: (排名DataFrame, 当前应持仓)
    """
    data_start = (datetime.today() - timedelta(days=180)).strftime('%Y-%m-%d')
    # 如果是15:00收盘后，使用今天数据；否则使用昨天数据
    now = datetime.now()
    if now.hour >= 15:
        end_date = datetime.today().strftime('%Y-%m-%d')
    else:
        end_date = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        df = get_data_tushare(symbol, data_start, end_date)
        if df is not None:
            etf_data[symbol] = df

    if len(etf_data) < 4:
        print("数据不足")
        return None, None

    # 找到最后一个周五
    common_dates = set.intersection(*[set(df.index) for df in etf_data.values()])
    fridays = sorted([d for d in common_dates if d.weekday() == 4])
    last_friday = fridays[-1]

    # 计算因子
    factors = calc_all_factors(etf_data, last_friday)
    factors = zscore_normalize(factors)

    # 排序
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)

    print(f"\n{'='*60}")
    print(f"最新排名 ({last_friday.strftime('%Y-%m-%d')} 收盘)")
    print(f"{'='*60}")
    print(f"{'排名':<6} {'ETF':<15} {'得分':<10} {'Bias':<10} {'Slope':<10} {'Efficiency':<10}")
    print("-" * 60)

    for i, (symbol, f) in enumerate(sorted_etfs, 1):
        print(f"{i:<6} {f['name']:<15} {f['total_score']:>8.3f}   {f['bias_z']:>8.3f}   {f['slope_z']:>8.3f}   {f['efficiency_z']:>8.3f}")

    best = sorted_etfs[0][0]
    print(f"\n🎯 应买入: {ETF_POOL[best]} ({best})")
    print(f"\n📋 下周一操作:")
    print(f"   1. 14:50-15:00 卖出当前持仓（如不是{ETF_POOL[best]}）")
    print(f"   2. 以收盘价买入 {ETF_POOL[best]}")

    return sorted_etfs, best


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='策略3收盘版回测')
    parser.add_argument('--rank', action='store_true', help='获取当前排名（实盘用）')
    args = parser.parse_args()

    if args.rank:
        get_current_ranking()
    else:
        result = run_backtest()

        if result:
            # 保存结果
            output_dir = '/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber/docs/策略研究/ETF三因子轮动策略/数据结果'
            os.makedirs(output_dir, exist_ok=True)

            df_result = pd.DataFrame([{
                'strategy': result['strategy'],
                'final_nav': result['final_nav'],
                'annual': result['annual'],
                'max_dd': result['max_dd'],
                'sharpe': result['sharpe'],
                'trades': result['trades'],
                'win_rate': result['win_rate'],
                'avg_holding': result['avg_holding']
            }])
            df_result.to_csv(f'{output_dir}/策略3_收盘版_结果.csv', index=False, encoding='utf-8-sig')
            print(f"\n✓ 结果已保存: {output_dir}/策略3_收盘版_结果.csv")

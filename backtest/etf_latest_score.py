"""
ETF三因子动量轮动策略 - 最新评分计算
2026-03-27
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import tushare as ts
from datetime import datetime, timedelta
import sys
import warnings
warnings.filterwarnings('ignore')

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890.SH': '红利低波ETF',
    '159949.SZ': '创业板50ETF',
    '513100.SH': '纳指ETF',
    '518880.SH': '黄金ETF'
}

BIAS_N = 20
MOMENTUM_DAY = 25
SLOPE_N = 20
WEIGHT_BIAS = 0.30
WEIGHT_SLOPE = 0.30
WEIGHT_EFFICIENCY = 0.40


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
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'vol': 'volume'})
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


def calc_all_factors(etf_data_dict):
    factors = {}
    for symbol, name in ETF_POOL.items():
        if symbol not in etf_data_dict or etf_data_dict[symbol] is None:
            continue
        df = etf_data_dict[symbol]
        if len(df) < max(BIAS_N, SLOPE_N, MOMENTUM_DAY):
            continue

        # 最新价格
        latest_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2] if len(df) > 1 else latest_price
        daily_change = (latest_price / prev_price - 1) * 100

        factors[symbol] = {
            'name': name,
            'latest_price': latest_price,
            'daily_change': daily_change,
            'bias': calc_bias_momentum(df['close']),
            'slope': calc_slope_momentum(df['close']),
            'efficiency': calc_efficiency_momentum(df)
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


def format_factor(val, width=10):
    """格式化因子值显示"""
    if abs(val) < 10:
        return f"{val:>{width}.4f}"
    elif abs(val) < 100:
        return f"{val:>{width}.2f}"
    else:
        return f"{val:>{width}.1f}"


def print_score_table(factors):
    """打印评分表格"""
    print("\n" + "=" * 100)
    print(f"ETF三因子动量评分表 - 评估日期: {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 100)

    # 表头
    print(f"\n{'排名':<4} {'代码':<12} {'名称':<12} {'最新价':<10} {'日涨跌':<8} {'bias得分':<10} {'slope得分':<10} {'效率得分':<10} {'总分':<10}")
    print("-" * 100)

    # 按总分排序
    sorted_etfs = sorted(factors.items(), key=lambda x: x[1]['total_score'], reverse=True)

    for rank, (symbol, f) in enumerate(sorted_etfs, 1):
        daily_str = f"{f['daily_change']:+.2f}%"
        print(f"{rank:<4} {symbol:<12} {f['name']:<12} {f['latest_price']:<10.3f} {daily_str:<8} "
              f"{format_factor(f['bias'], 10)} {format_factor(f['slope'], 10)} "
              f"{format_factor(f['efficiency'], 10)} {format_factor(f['total_score'], 10)}")

    print("-" * 100)

    # 打印因子标准化后的z-score
    print(f"\n{'标准化因子得分 (Z-Score)':}")
    print(f"{'排名':<4} {'代码':<12} {'名称':<12} {'bias_z':<10} {'slope_z':<10} {'efficiency_z':<10} {'总分':<10}")
    print("-" * 80)

    for rank, (symbol, f) in enumerate(sorted_etfs, 1):
        print(f"{rank:<4} {symbol:<12} {f['name']:<12} "
              f"{f['bias_z']:>10.3f} {f['slope_z']:>10.3f} {f['efficiency_z']:>10.3f} {f['total_score']:>10.3f}")

    print("-" * 80)

    # 建议持仓
    print(f"\n{'='*80}")
    print(f"【交易建议】")
    print(f"{'='*80}")

    top2 = sorted_etfs[:2]
    print(f"\n🏆 排名前2名（建议持仓50%+50%）:")
    for rank, (symbol, f) in enumerate(top2, 1):
        print(f"   {rank}. {f['name']} ({symbol}) - 得分: {f['total_score']:.3f}")

    print(f"\n⚠️  排名后2名（观望/卖出）:")
    for rank, (symbol, f) in enumerate(sorted_etfs[2:], 3):
        print(f"   {rank}. {f['name']} ({symbol}) - 得分: {f['total_score']:.3f}")

    # 计算得分差距
    score_diff = top2[0][1]['total_score'] - top2[1][1]['total_score']
    print(f"\n📊 得分分析:")
    print(f"   - 第1名与第2名得分差距: {score_diff:.3f}")
    print(f"   - 第2名与第3名得分差距: {top2[1][1]['total_score'] - sorted_etfs[2][1]['total_score']:.3f}")

    return sorted_etfs


def main():
    print("=" * 80)
    print("ETF三因子动量轮动策略 - 最新评分")
    print("=" * 80)

    # 计算数据获取时间范围（需要至少30个交易日数据）
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

    # 获取数据
    print(f"\n正在从tushare获取ETF数据...")
    print(f"数据范围: {start_date} ~ {end_date}")
    print()

    etf_data = {}
    for symbol, name in ETF_POOL.items():
        df = get_etf_data_tushare(symbol, start_date, end_date)
        if df is not None:
            etf_data[symbol] = df
            print(f"  ✓ {name} ({symbol}): 共 {len(df)} 条记录, 最新日期 {df.index[-1].strftime('%Y-%m-%d')}, 最新价 {df['close'].iloc[-1]:.3f}")

    if len(etf_data) < 4:
        print(f"\n❌ 数据不足，只有 {len(etf_data)}/4 只ETF")
        return

    # 计算因子
    print("\n正在计算三因子得分...")
    factors = calc_all_factors(etf_data)
    factors = zscore_normalize(factors)

    # 打印结果
    sorted_etfs = print_score_table(factors)

    # 保存结果
    result_data = []
    for rank, (symbol, f) in enumerate(sorted_etfs, 1):
        result_data.append({
            '排名': rank,
            '代码': symbol,
            '名称': f['name'],
            '最新价': f['latest_price'],
            '日涨跌': f"{f['daily_change']:.2f}%",
            'bias_raw': f['bias'],
            'slope_raw': f['slope'],
            'efficiency_raw': f['efficiency'],
            'bias_z': f['bias_z'],
            'slope_z': f['slope_z'],
            'efficiency_z': f['efficiency_z'],
            '总分': f['total_score']
        })

    result_df = pd.DataFrame(result_data)
    result_df.to_csv('etf_latest_score_2026-03-27.csv', index=False, encoding='utf-8-sig')
    print(f"\n✅ 评分结果已保存: etf_latest_score_2026-03-27.csv")


if __name__ == '__main__':
    main()

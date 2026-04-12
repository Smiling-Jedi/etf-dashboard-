"""
ETF三因子动量轮动策略 v3 - 收益优化版
优化目标：接近文章“7年13倍”的收益表现

调整点：
1. 【关键】交易成本降低到 0.03% (ETF无印花税，佣金低)
2. 【关键】BIAS_N 参数调整为 20 (使用月线乖离，反应更灵敏)
3. 保持每日评估的频率
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import akshare as ak
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
import warnings
warnings.filterwarnings('ignore')

# ============ 策略参数配置 ============
ETF_POOL = {
    '512890': '红利低波ETF',
    '159949': '创业板50ETF', 
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

# 因子参数
BIAS_N = 20          # 【调整】乖离率均线周期：60 -> 20 (更灵敏)
MOMENTUM_DAY = 25    # 动量计算周期
SLOPE_N = 20         # 斜率计算周期

# 因子权重
WEIGHT_BIAS = 0.3
WEIGHT_SLOPE = 0.3
WEIGHT_EFFICIENCY = 0.4

# 调仓阈值
SWITCH_THRESHOLD = 1.5

# 交易费率 (单边)
# ETF一般佣金万1~万3，无印花税。滑点设万1。总计万2~万3。
COMMISSION_RATE = 0.0003 

# 回测参数
START_DATE = '2019-01-01'
END_DATE = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
INITIAL_CAPITAL = 100000


def get_etf_data(symbol, start_date, end_date):
    """获取ETF历史数据"""
    try:
        df = ak.fund_etf_hist_em(symbol=symbol, period="daily", 
                                  start_date=start_date.replace('-', ''),
                                  end_date=end_date.replace('-', ''),
                                  adjust="qfq")
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.set_index('日期')
        df = df.rename(columns={
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume'
        })
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"获取 {symbol} 数据失败: {e}")
        return None


def calc_bias_momentum(close_prices):
    """
    计算乖离动量因子
    """
    if len(close_prices) < BIAS_N:
        return 0
    
    # 计算乖离度
    # bias = price / ma
    bias = close_prices / close_prices.rolling(window=BIAS_N, min_periods=1).mean()
    
    if len(bias) < MOMENTUM_DAY:
        return 0
    
    bias_recent = bias.iloc[-MOMENTUM_DAY:]
    x = np.arange(MOMENTUM_DAY).reshape(-1, 1)
    y = (bias_recent / bias_recent.iloc[0]).values
    
    lr = LinearRegression()
    lr.fit(x, y)
    bias_score = lr.coef_[0] * 10000
    
    return float(np.real(bias_score))


def calc_slope_momentum(close_prices):
    """
    计算斜率动量因子
    """
    if len(close_prices) < SLOPE_N:
        return 0
    
    prices = close_prices.iloc[-SLOPE_N:]
    
    # 价格标准化
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
    """
    计算效率动量因子
    """
    if len(df) < MOMENTUM_DAY:
        return 0
    
    df_recent = df.iloc[-MOMENTUM_DAY:].copy()
    
    pivot = (df_recent['open'] + df_recent['high'] + df_recent['low'] + df_recent['close']) / 4.0
    
    momentum = 100 * np.log(pivot.iloc[-1] / pivot.iloc[0])
    
    log_pivot = np.log(pivot)
    direction = abs(log_pivot.iloc[-1] - log_pivot.iloc[0])
    volatility = log_pivot.diff().abs().sum()
    
    efficiency_ratio = direction / volatility if volatility > 0 else 0
    
    score = momentum * efficiency_ratio
    
    return float(score)


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


def select_best_etf(factors, current_holding):
    """选择最优ETF"""
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
    
    # 调仓阈值判断
    if current_score <= 0:
        if best_score > 0:
            return best_symbol
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding
    else:
        if best_score > current_score * SWITCH_THRESHOLD:
            return best_symbol
        return current_holding


def run_backtest():
    """运行回测"""
    print("=" * 60)
    print("ETF三因子动量轮动策略 v3 (收益优化版)")
    print("=" * 60)
    print(f"\n回测区间: {START_DATE} ~ {END_DATE}")
    print(f"佣金费率: {COMMISSION_RATE*10000:.0f} bps")
    print(f"BIAS参数: {BIAS_N}")
    
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    
    etf_data = {}
    for symbol, name in ETF_POOL.items():
        print(f"  获取 {name} ({symbol})...")
        df = get_etf_data(symbol, data_start, END_DATE)
        if df is not None:
            etf_data[symbol] = df
            
    if len(etf_data) < 2:
        return
    
    common_dates = None
    for symbol, df in etf_data.items():
        dates = set(df.index)
        if common_dates is None:
            common_dates = dates
        else:
            common_dates = common_dates.intersection(dates)
    
    trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])
    print(f"\n共 {len(trade_dates)} 个交易日")
    
    capital = INITIAL_CAPITAL
    holding = None
    holding_shares = 0
    buy_price = 0
    
    nav_history = []
    trade_log = []
    
    for i, date in enumerate(trade_dates):
        factors = calc_all_factors(etf_data, date)
        if factors:
            factors = zscore_normalize(factors)
            target = select_best_etf(factors, holding)
            
            if target != holding:
                # 卖出
                if holding and holding_shares > 0:
                    sell_price = etf_data[holding].loc[date, 'close']
                    # 卖出扣费
                    capital = holding_shares * sell_price * (1 - COMMISSION_RATE)
                    pnl = (sell_price / buy_price - 1) * 100
                    trade_log.append({
                        'date': date,
                        'action': '卖出',
                        'symbol': holding,
                        'name': ETF_POOL[holding],
                        'price': sell_price,
                        'shares': holding_shares,
                        'capital': capital,
                        'pnl': pnl
                    })
                
                # 买入
                if target and target in etf_data:
                    buy_price = etf_data[target].loc[date, 'close']
                    # 买入扣费
                    holding_shares = int(capital * (1 - COMMISSION_RATE) / buy_price)
                    capital = capital - holding_shares * buy_price
                    holding = target
                    trade_log.append({
                        'date': date,
                        'action': '买入',
                        'symbol': target,
                        'name': ETF_POOL[target],
                        'price': buy_price,
                        'shares': holding_shares,
                        'capital': capital
                    })
        
        # 每日净值
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
    
    nav_df = pd.DataFrame(nav_history)
    nav_df.set_index('date', inplace=True)
    
    # 结果指标
    final_nav = nav_df['nav'].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365
    annual_return = ((final_nav ** (1/years)) - 1) * 100
    daily_returns = nav_df['nav'].pct_change().dropna()
    sharpe = (daily_returns.mean() * 252 - 0.03) / (daily_returns.std() * np.sqrt(252))
    
    rolling_max = nav_df['nav'].cummax()
    max_drawdown = ((nav_df['nav'] - rolling_max) / rolling_max).min() * 100

    print("\n" + "=" * 60)
    print("V3 回测结果")
    print("=" * 60)
    print(f"期末净值: {final_nav:.4f}")
    print(f"年化收益率: {annual_return:.2f}%")
    print(f"夏普比率: {sharpe:.2f}")
    print(f"最大回撤: {max_drawdown:.2f}%")
    
    plt.figure(figsize=(14, 10))
    
    plt.subplot(2, 1, 1)
    plt.plot(nav_df.index, nav_df['nav'], label=f'策略V3 (年化{annual_return:.1f}%)', color='#D62828', linewidth=2)
    plt.title(f'策略V3: 净值 {final_nav:.2f}倍 | 年化 {annual_return:.1f}% | 夏普 {sharpe:.2f}', fontsize=14, fontweight='bold')
    plt.ylabel('净值')
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left')
    
    plt.subplot(2, 1, 2)
    plt.fill_between(nav_df.index, ((nav_df['nav'] - rolling_max) / rolling_max) * 100, 0, alpha=0.5, color='#E94F37')
    plt.title(f'回撤曲线 (最大回撤: {max_drawdown:.1f}%)', fontsize=12)
    plt.xlabel('日期')
    plt.ylabel('回撤 (%)')
    plt.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('backtest_result_v3.png', dpi=150, bbox_inches='tight')
    print(f"\n结果图已保存: backtest_result_v3.png")
    
    nav_df.to_excel('nav_history_v3.xlsx')

if __name__ == '__main__':
    run_backtest()

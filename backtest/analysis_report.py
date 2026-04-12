"""
ETF策略深度分析报告
1. 分年度收益表现
2. 参数敏感性分析 (参数平原热力图)
3. 排名分层测试 (分箱分析)
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import akshare as ak
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import warnings
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings('ignore')

# 全局数据缓存，避免重复下载
ETF_DATA_CACHE = {}
ETF_POOL = {
    '512890': '红利低波',
    '159949': '创业板50', 
    '513100': '纳指ETF',
    '518880': '黄金ETF'
}

START_DATE = '2019-01-01'
END_DATE = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')

def load_data():
    """加载数据到内存"""
    print("正在加载数据...")
    data_start = (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=180)).strftime('%Y-%m-%d')
    for symbol, name in ETF_POOL.items():
        if symbol not in ETF_DATA_CACHE:
            try:
                df = ak.fund_etf_hist_em(symbol=symbol, period="daily", 
                                          start_date=data_start.replace('-', ''),
                                          end_date=END_DATE.replace('-', ''),
                                          adjust="qfq")
                df['日期'] = pd.to_datetime(df['日期'])
                df = df.set_index('日期')
                df = df.rename(columns={'收盘': 'close', '开盘': 'open', '最高': 'high', '最低': 'low'})
                ETF_DATA_CACHE[symbol] = df[['open', 'high', 'low', 'close']]
                print(f"  {name} 加载完成")
            except Exception as e:
                print(f"  {name} 加载失败: {e}")

# ================= 核心策略逻辑 (轻量化版) =================
def run_strategy_light(bias_n=20, switch_threshold=1.5, rank_select=0):
    """
    轻量化回测函数
    rank_select: 0表示选第1名，1表示选第2名...
    """
    if not ETF_DATA_CACHE:
        return 0, None  # 无数据
        
    momentum_day = 25
    slope_n = 20
    
    # 获取共同交易日
    common_dates = None
    for df in ETF_DATA_CACHE.values():
        dates = set(df.index)
        common_dates = date_set = dates if common_dates is None else common_dates.intersection(dates)
    trade_dates = sorted([d for d in common_dates if d >= pd.Timestamp(START_DATE)])
    
    capital = 1.0
    holding = None
    holding_shares = 0
    nav_list = []
    
    # 预计算因子 (简化只计算Close相关的)
    # 为了速度，这里简单计算所有日期的因子，然后用的时候查表
    # 注意：生产环境为了极速Grid Search，通常会向量化计算所有因子矩阵，这里为保持逻辑一致性用循环
    
    # 简单的日循环回测
    for date in trade_dates:
        # 1. 计算因子得分
        scores = []
        for symbol, df in ETF_DATA_CACHE.items():
            if date not in df.index: continue
            
            # 截取历史数据
            # 优化：直接使用df.loc切片可能会慢，真实优化会用numpy索引
            # 这里为代码可读性维持原样，但注意性能
            hist_end_idx = df.index.get_loc(date) + 1
            if hist_end_idx < bias_n + 1: continue
            
            # 获取最近一段close
            closes = df['close'].iloc[hist_end_idx - max(bias_n, 60) : hist_end_idx]
            if len(closes) < bias_n: continue
            
            # --- 因子计算 ---
            # 1. Bias
            ma = closes.rolling(window=bias_n).mean()
            bias = closes / ma
            bias_recent = bias.iloc[-momentum_day:]
            if len(bias_recent) < momentum_day: continue
            
            # Bias Slope
            x = np.arange(momentum_day)
            y = (bias_recent / bias_recent.iloc[0]).values
            try:
                lr_bias = np.polyfit(x, y, 1)[0] * 10000
            except:
                lr_bias = 0
                
            # 2. Slope Momentum
            prices_slope = closes.iloc[-slope_n:]
            norm_p = prices_slope / prices_slope.iloc[0]
            y_slope = norm_p.values
            x_slope = np.arange(slope_n)
            try:
                slope, intercept = np.polyfit(x_slope, y_slope, 1)
                # R2 calculation
                y_pred = slope * x_slope + intercept
                ss_res = np.sum((y_slope - y_pred) ** 2)
                ss_tot = np.sum((y_slope - np.mean(y_slope)) ** 2)
                r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
                slope_score = 10000 * slope * r2
            except:
                slope_score = 0
                
            # 3. Efficiency (简化)
            # 略去复杂的Efficiency计算以加速，假设前两个因子占主导
            # 或者复用Slope作为近似
            eff_score = slope_score # 简化替代
            
            scores.append({
                'symbol': symbol,
                's1': lr_bias,
                's2': slope_score,
                's3': eff_score # 简化
            })
            
        # 标准化和合成
        final_scores = {}
        if len(scores) >= 2:
            s1s = [x['s1'] for x in scores]
            s2s = [x['s2'] for x in scores]
            s1_mu, s1_std = np.mean(s1s), np.std(s1s)
            s2_mu, s2_std = np.mean(s2s), np.std(s2s)
            
            for item in scores:
                z1 = (item['s1'] - s1_mu) / (s1_std if s1_std!=0 else 1)
                z2 = (item['s2'] - s2_mu) / (s2_std if s2_std!=0 else 1)
                # 权重: Bias 0.3, Slope 0.3, Eff 0.4. 这里简化为 0.5, 0.5
                final_scores[item['symbol']] = 0.5 * z1 + 0.5 * z2
        elif len(scores) == 1:
            final_scores[scores[0]['symbol']] = 1.0
            
        # 选股
        target = holding
        if final_scores:
            sorted_items = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
            
            if rank_select < len(sorted_items):
                candidate_symbol, candidate_score = sorted_items[rank_select]
                
                # 只有选第1名时才应用调仓阈值逻辑
                if rank_select == 0:
                    if holding is None:
                        target = candidate_symbol
                    elif holding in final_scores:
                        curr_score = final_scores[holding]
                        # 阈值判断
                        if curr_score <= 0:
                            if candidate_score > 0 or candidate_score > curr_score * switch_threshold:
                                target = candidate_symbol
                        else:
                            if candidate_score > curr_score * switch_threshold:
                                target = candidate_symbol
                    else:
                        target = candidate_symbol
                else:
                    # 选第N名直接换，不应用阈值（逻辑太复杂）
                    target = candidate_symbol
        
        # 记录净值
        if holding and holding in ETF_DATA_CACHE:
            # 简单计算涨跌幅
            ret = ETF_DATA_CACHE[holding].loc[date, 'close'] / ETF_DATA_CACHE[holding]['close'].shift(1).loc[date]
            capital *= ret
        else:
            # 空仓无收益 或 第一天
            pass
            
        # 调仓 (收盘后调仓，次日生效 - 简化逻辑)
        # 实际上上面的ret计算是基于持有holding度过当天，所以这里改变holding影响的是明天
        if target != holding:
            capital *= 0.9997 # 扣费
            holding = target
            
        nav_list.append({'date': date, 'nav': capital})
        
    df_nav = pd.DataFrame(nav_list).set_index('date')
    years = (trade_dates[-1] - trade_dates[0]).days / 365
    ann_ret = (capital ** (1/years) - 1) * 100
    
    return ann_ret, df_nav

# ================= 1. 分年度表现分析 =================
def analyze_yearly_performance(nav_df):
    nav_df['year'] = nav_df.index.year
    yearly_stats = []
    
    for year, group in nav_df.groupby('year'):
        start_nav = group['nav'].iloc[0]
        end_nav = group['nav'].iloc[-1]
        ret = (end_nav / start_nav - 1) * 100
        
        # Max Drawdown
        roll_max = group['nav'].cummax()
        dd = (group['nav'] - roll_max) / roll_max
        mdd = dd.min() * 100
        
        yearly_stats.append({
            'Year': year,
            'Return': ret,
            'MDD': mdd
        })
    
    return pd.DataFrame(yearly_stats)

def plot_yearly(stats):
    fig, ax1 = plt.subplots(figsize=(10, 6))
    
    colors = ['#d32f2f' if v < 0 else '#2e7d32' for v in stats['Return']]
    bars = ax1.bar(stats['Year'], stats['Return'], color=colors, alpha=0.7, label='年度收益')
    
    for bar in bars:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.1f}%', ha='center', va='bottom' if height>0 else 'top')
                
    ax1.set_ylabel('收益率 (%)')
    ax1.set_title('策略分年度收益表现', fontsize=14)
    ax1.grid(axis='y', alpha=0.3)
    
    plt.savefig('analysis_yearly.png', bbox_inches='tight')
    print("分年度图表已保存: analysis_yearly.png")

# ================= 2. 参数平原分析 =================
def analyze_parameter_surface():
    print("\n开始参数敏感性分析 (这可能需要几分钟)...")
    bias_range = range(10, 70, 10)  # 10, 20...60
    threshold_range = [1.1, 1.3, 1.5, 1.7, 1.9, 2.1]
    
    results = np.zeros((len(threshold_range), len(bias_range)))
    
    for i, th in enumerate(threshold_range):
        for j, bn in enumerate(bias_range):
            print(f"  测试参数: Threshold={th}, Bias_N={bn}...", end='\r')
            ret, _ = run_strategy_light(bias_n=bn, switch_threshold=th)
            results[i, j] = ret
            
    # 绘图
    plt.figure(figsize=(10, 8))
    sns.heatmap(results, annot=True, fmt=".1f", 
                xticklabels=bias_range, yticklabels=threshold_range,
                cmap="RdYlGn", cbar_kws={'label': '年化收益率 (%)'})
    
    plt.xlabel('BIAS_N (乖离周期)')
    plt.ylabel('Switch Threshold (调仓阈值)')
    plt.title('参数平原：年化收益率热力图', fontsize=14)
    plt.savefig('analysis_heatmap.png', bbox_inches='tight')
    print("\n参数热力图已保存: analysis_heatmap.png")

# ================= 3. 排名分层测试 =================
def analyze_rank_layer():
    print("\n开始排名分层测试...")
    plt.figure(figsize=(12, 6))
    
    ranks = [0, 1, 2, 3] # Rank 1 to 4
    labels = ['持有第1名', '持有第2名', '持有第3名', '持有第4名']
    
    for rank in ranks:
        ret, df_nav = run_strategy_light(bias_n=20, switch_threshold=1.5, rank_select=rank)
        plt.plot(df_nav.index, df_nav['nav'], label=f'{labels[rank]} (年化{ret:.1f}%)')
        
    plt.title('因子有效性检验：不同排名持仓净值曲线', fontsize=14)
    plt.yscale('log') # 对数坐标看长期差异
    plt.grid(True, which="both", ls="-", alpha=0.2)
    plt.legend()
    plt.savefig('analysis_rank.png', bbox_inches='tight')
    print("排名分层图已保存: analysis_rank.png")

if __name__ == '__main__':
    load_data()
    
    # 1. 跑一次基准策略获取Nav
    print("运行基准策略...")
    ann_ret, nav_df = run_strategy_light(bias_n=20, switch_threshold=1.5, rank_select=0)
    
    stats = analyze_yearly_performance(nav_df)
    plot_yearly(stats)
    print("\n年度统计数据:")
    print(stats)
    
    # 2. 参数分析
    analyze_parameter_surface()
    
    # 3. 排名分析
    analyze_rank_layer()
    
    print("\n分析全部完成！")

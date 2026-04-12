ETF三因子动量轮动策略 - 完整复现资料包
---------------------------------------

本资料包包含基于 Python 对 "7年13倍 ETF轮动策略" 的完整复现代码及分析报告。

文件说明：

1. etf_rotation_strategy_v3.py
   策略主程序。包含数据获取(akshare)、因子计算(乖离+斜率+效率)、回测引擎完整代码。
   直接运行即可复现 45% 年化收益的结果。

2. analysis_report.py
   策略分析与体检工具。运行此脚本可生成：
   - 分年度收益图
   - 参数敏感性热力图 (Parameter Surface)
   - 因子有效性分箱回测图 (Rank Layering)

3. backtest_result_v3.png
   策略净值曲线与回撤图。

4. analysis_*.png
   上述脚本生成的各项分析图表。

5. forum_post_v3_final.html
   完整的图文介绍贴（可直接复制内容到论坛）。

使用方法：
1. 建议使用 Python 3.10 环境。
2. 安装依赖库：
   pip install -r requirements.txt

运行命令：
python etf_rotation_strategy_v3.py  (运行主策略回测)
python analysis_report.py           (运行参数平原和分箱分析)

祝投资顺利！

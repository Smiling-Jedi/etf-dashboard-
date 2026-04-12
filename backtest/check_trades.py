: " #!/usr/bin/env python3

import sqlite3
from datetime import datetime, timedelta

# 连接数据库
conn = sqlite3.connect('/Users/jediyang/ClaudeCode/Project-Makemoney/lightsaber/data/lightsaber.db')
cursor = conn.cursor()

# 获取所有表名
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("数据库中的表:", [t[0] for t in tables])

# 查询最近一周的交易记录
one_week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
print(f"\n查询日期范围: {one_week_ago} 至今\n")

cursor.execute("""
SELECT
  t.id,
  t.trade_date,
  p.stock_symbol,
  s.name,
  t.trade_type,
  t.shares,
  t.price,
  t.trading_cost,
  t.total_cost,
  t.is_swing,
  t.target_sell_price,
  t.stop_loss_price
FROM trades t
JOIN positions p ON t.position_id = p.id
LEFT JOIN stocks s ON p.stock_symbol = s.symbol
WHERE t.trade_date >= ?
ORDER BY t.trade_date DESC, t.id DESC
""", (one_week_ago,))

trades = cursor.fetchall()

if not trades:
    print("最近一周没有新增交易记录")
else:
    print(f"共找到 {len(trades)} 条交易记录\n")
    print(f"{'ID':<5} {'日期':<12} {'代码':<10} {'名称':<10} {'类型':<6} {'数量':<8} {'价格':<10} {'手续费':<8} {'总成本':<12} {'波段':<6}")
    print("-" * 100)
    for t in trades:
        trade_id, date, symbol, name, trade_type, shares, price, cost, total, is_swing, target, stop = t
        swing_mark = "是" if is_swing else "否"
        print(f"{trade_id:<5} {date:<12} {symbol:<10} {(name or '-'):<10} {trade_type:<6} {shares:<8} {price:<10.2f} {cost or 0:<8.2f} {total:<12.2f} {swing_mark:<6}")
        if target or stop:
            print(f"       -> 目标价: {target or '-':<10} 止损价: {stop or '-'}")

conn.close() "

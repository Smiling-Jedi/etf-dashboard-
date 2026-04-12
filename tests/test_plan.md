# ETF策略独立数据库测试计划

## 测试范围
1. 数据文件完整性和格式验证
2. update_v2.py 功能测试
3. HTML页面生成测试
4. 边界条件和异常处理

## 测试环境
- Python 3.x
- Git 已配置
- 网络连接正常

## 测试用例清单

### TC-001: 数据文件格式验证
**目的**: 验证所有JSON文件格式正确
**步骤**:
1. 检查 trades.json 格式
2. 检查 positions.json 格式
3. 检查 capital.json 格式
4. 检查 weekly_scores.json 格式
5. 检查 pnl_history.json 格式

**预期结果**: 所有文件均为有效JSON，结构符合预期

### TC-002: 持仓计算验证
**目的**: 验证持仓数据计算正确
**步骤**:
1. 读取 trades.json 中所有买入记录
2. 计算总股数 = sum(shares)
3. 计算加权平均成本 = sum(price*shares)/total_shares
4. 对比 positions.json 中的数据

**预期结果**: 计算结果与positions.json一致

### TC-003: update_v2.py 导入测试
**目的**: 验证脚本可以正常导入
**步骤**:
1. 在etf-dashboard目录运行: python3 -c "import update_v2"

**预期结果**: 无导入错误

### TC-004: HTML生成测试
**目的**: 验证HTML页面正确生成
**步骤**:
1. 运行 python3 update_v2.py
2. 检查 index.html 是否生成
3. 验证HTML包含关键元素:
   - 策略标识
   - 持仓信息
   - 排名表格
   - 交易建议
   - 交易记录

**预期结果**: 页面生成成功，包含所有必要元素

### TC-005: 持仓盈亏计算测试
**目的**: 验证盈亏计算逻辑
**步骤**:
1. 持仓: 60000股 @ 1.5430
2. 当前价: 1.4870 (假设周五收盘价)
3. 市值 = 60000 * 1.4870 = 89220
4. 成本 = 92580
5. 盈亏 = 89220 - 92580 = -3360
6. 盈亏率 = -3360 / 92580 = -3.63%

**预期结果**: 计算结果与pnl_history.json一致

### TC-006: 阈值判断测试
**目的**: 验证1.5倍阈值逻辑
**步骤**:
1. 当前持仓得分: 0.625
2. 第1名得分: 0.664
3. 阈值 = 0.625 * 1.5 = 0.938
4. 判断: 0.664 < 0.938

**预期结果**: should_trade = false, signal = "HOLD"

### TC-007: 空数据测试
**目的**: 验证空数据情况下的处理
**步骤**:
1. 备份 data/trades.json
2. 清空 trades.json 中的 trades 数组
3. 运行 update_v2.py
4. 检查页面是否正常显示

**预期结果**: 页面显示"暂无交易记录"，无崩溃

### TC-008: Git状态测试
**目的**: 验证Git工作流正常
**步骤**:
1. 检查 git status
2. 确认 data/ 目录被跟踪
3. 确认更新后自动提交

**预期结果**: Git状态正常，变更被正确提交

### TC-009: 快捷命令测试
**目的**: 验证.command文件可执行
**步骤**:
1. 检查文件权限
2. 检查文件内容指向正确的脚本

**预期结果**: 文件可执行，指向 update_v2.py

### TC-010: 数据一致性测试
**目的**: 验证各文件间数据一致
**步骤**:
1. trades.json 总股数 = positions.json current.total_shares
2. capital.json total_invested = positions.json current.total_cost
3. weekly_scores.json holding_code = positions.json current.code

**预期结果**: 所有关联数据一致

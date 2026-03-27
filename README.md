# ETF轮动策略页面自动更新

## 使用方法

### 一键更新（推荐）

```bash
cd /Users/jediyang/ClaudeCode/Project-Makemoney/etf-dashboard
python3 update_dashboard.py
```

脚本会自动：
1. 获取4只ETF最新日线数据
2. 计算日/月/年涨跌幅
3. 计算三因子得分并排名
4. 生成新的HTML页面
5. Git提交并推送到GitHub
6. 页面1-2分钟后自动更新

### 更新频率

- **建议**：每周五收盘后运行一次
- **也可以**：任何时候想查看最新数据时运行

### 手动更新数据

如果不想运行脚本，可以手动编辑 `index.html`：

1. 打开 `index.html`
2. 找到排名表格部分
3. 修改涨跌幅数字：
   ```html
   <td class="change-col"><span class="change-up">+1.23%</span></td>  <!-- 日涨跌 -->
   <td class="change-col"><span class="change-down">-2.34%</span></td> <!-- 月涨跌 -->
   <td class="change-col"><span class="change-up">+15.6%</span></td>  <!-- 年涨跌 -->
   ```
4. 保存后执行：
   ```bash
   git add index.html
   git commit -m "Update: 手动更新数据"
   git push origin main
   ```

## 文件说明

| 文件 | 说明 |
|------|------|
| `index.html` | 监控页面（自动生成的） |
| `update_dashboard.py` | 自动更新脚本 |
| `README.md` | 本说明文件 |

## 访问地址

https://smiling-jedi.github.io/etf-dashboard-/

## 注意事项

1. 首次运行需要确保tushare可用（`pip install tushare pandas numpy scikit-learn`）
2. 如果GitHub推送失败，检查网络连接或手动执行git push
3. GitHub Pages更新有1-2分钟延迟，推送后稍等再刷新页面

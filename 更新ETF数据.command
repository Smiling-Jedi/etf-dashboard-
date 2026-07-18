#!/bin/bash
set -e

cd /Users/jediyang/ClaudeCode/Project-Makemoney/etf-dashboard || {
    echo "❌ 无法进入 etf-dashboard 目录"
    exit 1
}

echo "正在更新ETF数据并计算三因子得分..."

/usr/bin/python3 update_scores.py

echo ""
echo "✅ 更新完成，按回车键关闭..."
read

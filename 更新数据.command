#!/bin/bash
cd /Users/jediyang/ClaudeCode/Project-Makemoney/etf-dashboard

echo "======================================"
echo "  光剑系统数据更新"
echo "======================================"
echo ""
echo "请选择要更新的内容："
echo ""
echo "[1] 更新ETF轮动数据"
echo "[2] 导出投资中枢报告"
echo "[3] 全部更新（1+2）"
echo ""
read -p "输入选项 (1-3): " choice

case $choice in
    1)
        echo ""
        echo "正在更新ETF轮动数据..."
        python3 update_dashboard.py
        ;;
    2)
        echo ""
        echo "正在导出投资中枢报告..."
        python3 export_investment.py
        ;;
    3)
        echo ""
        echo "正在更新ETF轮动数据..."
        python3 update_dashboard.py
        echo ""
        echo "正在导出投资中枢报告..."
        python3 export_investment.py
        ;;
    *)
        echo "无效选项"
        ;;
esac

echo ""
read -p "按回车键关闭..."

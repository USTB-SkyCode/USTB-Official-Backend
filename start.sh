#!/bin/bash

# ========================================
# Flask 开发环境启动脚本
# ========================================

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}启动Flask开发服务器...${NC}"

# 设置环境变量
export FLASK_APP=run.py
export FLASK_ENV=development
export FLASK_DEBUG=True

# 进入Flask应用目录
cd "$(dirname "$0")"

# 检查虚拟环境
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo -e "${YELLOW}警告: 未检测到激活的Python虚拟环境${NC}"
fi

# 显示配置信息
echo -e "应用目录: $(pwd)"
echo -e "Python: $(which python3)"
echo -e "Flask版本: $(python3 -c 'import flask; print(flask.__version__)' 2>/dev/null || echo '未安装')"
echo ""

# 启动Flask应用
echo -e "${GREEN}正在启动Flask应用...${NC}"
echo "访问地址: http://127.0.0.1:5000"
echo "按 Ctrl+C 停止服务器"
echo ""

python3 run.py

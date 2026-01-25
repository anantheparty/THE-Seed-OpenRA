#!/bin/bash
# THE-Seed OpenRA Web Console - VNC 启动脚本

set -e

# 配置
DISPLAY_NUM=99
SCREEN_RES="1280x720x24"
VNC_PORT=5900
NOVNC_PORT=6080
WEB_PORT=8000

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  THE-Seed OpenRA Web Console           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"

# 清理旧进程
cleanup() {
    echo -e "${YELLOW}[*] 清理旧进程...${NC}"
    pkill -f "Xvfb :$DISPLAY_NUM" 2>/dev/null || true
    pkill -f "x11vnc.*:$DISPLAY_NUM" 2>/dev/null || true
    pkill -f "websockify.*$NOVNC_PORT" 2>/dev/null || true
    pkill -f "python.*$WEB_PORT" 2>/dev/null || true
    sleep 1
}

cleanup

# 启动 Xvfb
echo -e "${GREEN}[1/4] 启动 Xvfb 虚拟显示...${NC}"
Xvfb :$DISPLAY_NUM -screen 0 $SCREEN_RES &
sleep 1
echo "      DISPLAY=:$DISPLAY_NUM ($SCREEN_RES)"

# 启动 x11vnc
echo -e "${GREEN}[2/4] 启动 x11vnc...${NC}"
x11vnc -display :$DISPLAY_NUM -forever -shared -nopw -rfbport $VNC_PORT -bg -o /tmp/x11vnc.log
sleep 1
echo "      VNC 端口: $VNC_PORT"

# 启动 noVNC (websockify)
echo -e "${GREEN}[3/4] 启动 noVNC websockify...${NC}"
websockify --web=/usr/share/novnc $NOVNC_PORT localhost:$VNC_PORT &
sleep 1
echo "      noVNC 端口: $NOVNC_PORT"

# 启动自定义 Web 服务器
echo -e "${GREEN}[4/4] 启动 Web 控制台...${NC}"
cd "$(dirname "$0")"
python3 -m http.server $WEB_PORT &
sleep 1
echo "      Web 控制台端口: $WEB_PORT"

# 获取 IP
IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}✓ 启动完成！${NC}"
echo ""
echo -e "  ${YELLOW}Web 控制台:${NC}  http://$IP:$WEB_PORT/console.html"
echo -e "  ${YELLOW}纯 VNC:${NC}      http://$IP:$NOVNC_PORT/vnc.html"
echo ""
echo -e "  DISPLAY=:$DISPLAY_NUM 可以用来启动 GUI 程序"
echo -e "  例如: DISPLAY=:$DISPLAY_NUM xterm &"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "按 Ctrl+C 停止所有服务..."

# 等待并清理
trap cleanup EXIT
wait

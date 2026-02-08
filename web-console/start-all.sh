#!/bin/bash
# THE-Seed Web Console - 启动所有服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $1"
}

# 清理旧进程
cleanup() {
    log "清理旧进程..."
    pkill -f "Xvfb :99" 2>/dev/null || true
    pkill -f "x11vnc.*:99" 2>/dev/null || true
    pkill -f "websockify.*6080" 2>/dev/null || true
    pkill -f "http.server 8000" 2>/dev/null || true
    pkill -f "uvicorn.*8087" 2>/dev/null || true
    rm -f /tmp/.X99-lock 2>/dev/null || true
    sleep 2
}

# 启动 Xvfb
start_xvfb() {
    log "启动 Xvfb..."
    Xvfb :99 -screen 0 1280x720x24 -ac &
    sleep 2
    if DISPLAY=:99 xdpyinfo &>/dev/null; then
        log "Xvfb 已启动 (DISPLAY=:99)"
    else
        error "Xvfb 启动失败"
        exit 1
    fi
}

# 启动 x11vnc
start_vnc() {
    log "启动 x11vnc..."
    x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg -o /tmp/x11vnc.log
    sleep 1
    log "x11vnc 已启动 (端口 5900)"
}

# 启动 websockify (noVNC)
start_novnc() {
    log "启动 noVNC (websockify)..."
    websockify --web=/usr/share/novnc 6080 localhost:5900 &>/dev/null &
    sleep 1
    log "noVNC 已启动 (端口 6080)"
}

# 启动 Web Console
start_web() {
    log "启动 Web Console..."
    cd "$SCRIPT_DIR"
    python3 -m http.server 8000 &>/dev/null &
    sleep 1
    log "Web Console 已启动 (端口 8000)"
}

# 启动 Service API
start_api() {
    log "启动 Service API..."
    cd "$SCRIPT_DIR/api"
    
    # 检查并安装依赖
    if ! python3 -c "import fastapi" 2>/dev/null; then
        log "安装 API 依赖..."
        pip3 install -q -r requirements.txt
    fi
    
    python3 -m uvicorn service:app --host 0.0.0.0 --port 8087 &>/dev/null &
    sleep 2
    log "Service API 已启动 (端口 8087)"
}

# 主函数
main() {
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  THE-Seed Web Console - 启动所有服务   ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
    echo ""

    cleanup
    start_xvfb
    start_vnc
    start_novnc
    start_web
    start_api

    IP=$(hostname -I | awk '{print $1}')
    
    echo ""
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ 所有服务已启动${NC}"
    echo ""
    echo -e "  ${YELLOW}内网访问:${NC}"
    echo -e "    Web Console: http://$IP:8000/"
    echo -e "    VNC:         http://$IP:6080/vnc.html"
    echo ""
    echo -e "  ${YELLOW}公网访问:${NC}"
    echo -e "    https://openra.ananthe.party/"
    echo ""
    echo -e "  ${YELLOW}DISPLAY:${NC} :99"
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo ""
    echo "按 Ctrl+C 停止所有服务..."

    # 捕获退出信号
    trap cleanup EXIT
    
    # 保持运行
    while true; do
        sleep 60
        # 健康检查
        if ! pgrep -f "Xvfb :99" > /dev/null; then
            error "Xvfb 已停止，正在重启..."
            start_xvfb
            start_vnc
        fi
    done
}

main "$@"

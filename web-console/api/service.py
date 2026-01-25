#!/usr/bin/env python3
"""
THE-Seed Service Control API

提供服务控制接口：Git Pull, Build, Start, Stop, Restart
"""
import os
import subprocess
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json

# 配置
OPENRA_DIR = Path.home() / "theseed" / "THE-Seed-OpenRA" / "OpenCodeAlert"
THESEED_DIR = Path.home() / "theseed" / "THE-Seed-OpenRA"
DOTNET_PATH = Path.home() / ".dotnet" / "dotnet"

app = FastAPI(title="THE-Seed Service API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket 连接管理
connected_clients: set = set()


class CommandResult(BaseModel):
    success: bool
    message: str
    output: Optional[str] = None


async def run_command(cmd: list, cwd: Path = None, env: dict = None) -> CommandResult:
    """运行命令并返回结果"""
    try:
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        
        # 确保 dotnet 在 PATH 中
        full_env["PATH"] = f"{Path.home()}/.dotnet:{full_env.get('PATH', '')}"
        full_env["DOTNET_ROOT"] = str(Path.home() / ".dotnet")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=300)
        output = stdout.decode() if stdout else ""
        
        return CommandResult(
            success=process.returncode == 0,
            message="命令执行成功" if process.returncode == 0 else "命令执行失败",
            output=output[-2000:] if len(output) > 2000 else output  # 限制输出长度
        )
    except asyncio.TimeoutError:
        return CommandResult(success=False, message="命令超时")
    except Exception as e:
        return CommandResult(success=False, message=f"执行错误: {str(e)}")


async def broadcast(message: dict):
    """广播消息给所有连接的客户端"""
    for ws in connected_clients.copy():
        try:
            await ws.send_json(message)
        except:
            connected_clients.discard(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点用于实时日志推送"""
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理来自客户端的消息
            msg = json.loads(data)
            if msg.get("type") == "command":
                action = msg.get("payload", {}).get("action")
                if action:
                    result = await handle_action(action)
                    await websocket.send_json({
                        "type": "result",
                        "payload": result.dict()
                    })
    except WebSocketDisconnect:
        connected_clients.discard(websocket)


async def handle_action(action: str) -> CommandResult:
    """处理服务操作"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": f"执行: {action}"}})
    
    if action == "pull":
        return await git_pull()
    elif action == "build":
        return await build_openra()
    elif action == "start":
        return await start_game()
    elif action == "stop":
        return await stop_game()
    elif action == "restart":
        await stop_game()
        await asyncio.sleep(2)
        return await start_game()
    elif action == "start_ai":
        return await start_ai()
    elif action == "stop_ai":
        return await stop_ai()
    else:
        return CommandResult(success=False, message=f"未知操作: {action}")


@app.post("/api/pull")
async def git_pull() -> CommandResult:
    """Git Pull"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在拉取代码..."}})
    result = await run_command(["git", "pull"], cwd=THESEED_DIR)
    await broadcast({"type": "log", "payload": {"level": "success" if result.success else "error", "message": result.message}})
    return result


@app.post("/api/build")
async def build_openra() -> CommandResult:
    """编译 OpenRA"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在编译 OpenRA..."}})
    result = await run_command(["make"], cwd=OPENRA_DIR)
    await broadcast({"type": "log", "payload": {"level": "success" if result.success else "error", "message": result.message}})
    return result


@app.post("/api/start")
async def start_game() -> CommandResult:
    """启动游戏"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在启动游戏..."}})
    
    # 设置 DISPLAY 环境变量
    env = {"DISPLAY": ":99"}
    
    # 使用 nohup 启动游戏
    cmd = ["bash", "-c", f"cd {OPENRA_DIR} && nohup ./start.sh > /tmp/openra.log 2>&1 &"]
    result = await run_command(cmd, env=env)
    
    if result.success:
        await asyncio.sleep(3)  # 等待游戏启动
        await broadcast({"type": "log", "payload": {"level": "success", "message": "游戏已启动"}})
    
    return result


@app.post("/api/stop")
async def stop_game() -> CommandResult:
    """停止游戏"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在停止游戏..."}})
    result = await run_command(["pkill", "-f", "OpenRA.dll"])
    await broadcast({"type": "log", "payload": {"level": "success", "message": "游戏已停止"}})
    return CommandResult(success=True, message="游戏已停止")


@app.post("/api/restart")
async def restart_game() -> CommandResult:
    """重启游戏"""
    await stop_game()
    await asyncio.sleep(2)
    return await start_game()


@app.post("/api/start_ai")
async def start_ai() -> CommandResult:
    """启动 THE-Seed AI"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在启动 AI..."}})
    
    cmd = ["bash", "-c", f"cd {THESEED_DIR} && nohup python3 main.py > /tmp/theseed.log 2>&1 &"]
    result = await run_command(cmd)
    
    if result.success:
        await asyncio.sleep(2)
        await broadcast({"type": "log", "payload": {"level": "success", "message": "AI 已启动"}})
    
    return result


@app.post("/api/stop_ai")
async def stop_ai() -> CommandResult:
    """停止 THE-Seed AI"""
    await broadcast({"type": "log", "payload": {"level": "info", "message": "正在停止 AI..."}})
    result = await run_command(["pkill", "-f", "python3 main.py"])
    await broadcast({"type": "log", "payload": {"level": "success", "message": "AI 已停止"}})
    return CommandResult(success=True, message="AI 已停止")


@app.get("/api/status")
async def get_status():
    """获取服务状态"""
    # 检查 OpenRA 是否运行
    game_running = subprocess.run(
        ["pgrep", "-f", "OpenRA.dll"], 
        capture_output=True
    ).returncode == 0
    
    # 检查 AI 是否运行
    ai_running = subprocess.run(
        ["pgrep", "-f", "python3 main.py"], 
        capture_output=True
    ).returncode == 0
    
    # 检查 Xvfb 是否运行
    xvfb_running = subprocess.run(
        ["pgrep", "-f", "Xvfb :99"], 
        capture_output=True
    ).returncode == 0
    
    return {
        "game": "running" if game_running else "stopped",
        "ai": "running" if ai_running else "stopped",
        "vnc": "running" if xvfb_running else "stopped"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)

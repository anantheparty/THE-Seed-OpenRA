# -*- coding: utf-8 -*-
"""
Tactical Core 独立 API 客户端
为战术模块提供独立的 socket 通信能力，不依赖主程序。
"""

import socket
import json
import uuid
import time
from typing import Dict, List, Any, Optional

class TacticalClient:
    def __init__(self, host="localhost", port=7445):
        self.server_address = (host, port)
        self.api_version = "1.0"

    def _send_request(self, command: str, params: dict) -> dict:
        request_id = str(uuid.uuid4())
        request_data = {
            "apiVersion": self.api_version,
            "requestId": request_id,
            "command": command,
            "params": params,
            "language": "zh"
        }
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2.0)
                sock.connect(self.server_address)
                sock.sendall(json.dumps(request_data).encode('utf-8'))
                
                chunks = []
                while True:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk: break
                        chunks.append(chunk)
                    except socket.timeout:
                        break
                        
                response_str = b''.join(chunks).decode('utf-8')
                return json.loads(response_str)
        except Exception as e:
            # 战术模块允许偶尔通信失败，不抛出致命异常，仅返回空
            print(f"[TacticalClient] Error: {e}")
            return None

    def query_all_units(self, faction: str) -> Optional[List[dict]]:
        """查询指定阵营的所有单位（包含建筑等所有实体）"""
        # range="all" 确保获取所有单位
        params = {
            "targets": {
                "faction": faction,
                "range": "all" 
            }
        }
        resp = self._send_request("query_actor", params)
        if resp is None:
            return None
            
        data = resp.get("data", {})
        return data.get("actors", []) if data else []

    def attack_target(self, attacker_id: int, target_id: int) -> None:
        params = {
            "attackers": {"actorId": [attacker_id]},
            "targets": {"actorId": [target_id]}
        }
        self._send_request("attack", params)

    def move_unit(self, actor_id: int, direction: str, distance: int = 1, assault: bool = False, is_attack_move: bool = False) -> None:
        params = {
            "targets": {"actorId": [actor_id]},
            "direction": direction,
            "distance": distance,
            "isAttackMove": 1 if is_attack_move else 0,
            "isAssaultMove": 1 if assault else 0
        }
        self._send_request("move_actor", params)

import json
import socket
import time
import uuid
from typing import Any, Dict, List, Optional

from .models import Actor, Location, MapQueryResult, PlayerBaseInfo, ScreenInfoResult, TargetsQueryParam

API_VERSION = "1.0"


class GameAPIError(Exception):
    def __init__(self, code: str, message: str, details: Dict = None):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{code}: {message}")


class GameAPI:
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5

    @staticmethod
    def is_server_running(host: str = "localhost", port: int = 7445, timeout: float = 2.0) -> bool:
        try:
            request_data = {
                "apiVersion": API_VERSION,
                "requestId": str(uuid.uuid4()),
                "command": "ping",
                "params": {},
                "language": "zh",
            }
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((host, port))
                json_data = json.dumps(request_data)
                sock.sendall(json_data.encode("utf-8"))
                chunks = []
                while True:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except socket.timeout:
                        if chunks:
                            break
                        return False
                data = b"".join(chunks).decode("utf-8")
                try:
                    response = json.loads(data)
                    return response.get("status", 0) > 0 and "data" in response
                except json.JSONDecodeError:
                    return False
        except (socket.error, ConnectionRefusedError, OSError):
            return False
        except Exception:
            return False

    def __init__(self, host: str, port: int = 7445, language: str = "zh"):
        self.server_address = (host, port)
        self.language = language

    def _generate_request_id(self) -> str:
        return str(uuid.uuid4())

    def _receive_data(self, sock: socket.socket) -> str:
        chunks = []
        sock.settimeout(2.0)
        while True:
            try:
                chunk = sock.recv(32768)
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                if not chunks:
                    raise GameAPIError("TIMEOUT", "接收响应超时")
                break
        return b"".join(chunks).decode("utf-8")

    def _send_request(self, command: str, params: dict) -> dict:
        request_id = self._generate_request_id()
        request_data = {
            "apiVersion": API_VERSION,
            "requestId": request_id,
            "command": command,
            "params": params,
            "language": self.language,
        }
        retries = 0
        while retries < self.MAX_RETRIES:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(10)
                    sock.connect(self.server_address)
                    json_data = json.dumps(request_data)
                    sock.sendall(json_data.encode("utf-8"))
                    response_data = self._receive_data(sock)
                    try:
                        response = json.loads(response_data)
                        if not isinstance(response, dict):
                            raise GameAPIError("INVALID_RESPONSE", "服务器返回的响应格式无效")
                        if response.get("requestId") != request_id:
                            raise GameAPIError("REQUEST_ID_MISMATCH", "响应的请求ID不匹配")
                        if response.get("status", 0) < 0:
                            error = response.get("error", {})
                            raise GameAPIError(
                                error.get("code", "UNKNOWN_ERROR"),
                                error.get("message", "未知错误"),
                                error.get("details"),
                            )
                        data = response.get("data")
                        if isinstance(data, dict):
                            actors_list: List[Actor] = []
                            if "actors" in data:
                                for actor_data in data["actors"]:
                                    actor = Actor(actor_data["id"])
                                    actor.update_details(
                                        type=actor_data.get("type"),
                                        faction=actor_data.get("faction"),
                                        position=Location(**actor_data["position"]) if "position" in actor_data else None,
                                        hppercent=actor_data.get("hp") * 100 // actor_data.get("maxHp")
                                        if actor_data.get("maxHp")
                                        else 0,
                                        is_frozen=actor_data.get("isFrozen", False),
                                        activity=actor_data.get("activity"),
                                        order=actor_data.get("order"),
                                    )
                                    actors_list.append(actor)
                            if "frozenActors" in data:
                                for actor_data in data["frozenActors"]:
                                    actor_id = actor_data.get("id", -1)
                                    actor = Actor(actor_id)
                                    actor.update_details(
                                        type=actor_data.get("type"),
                                        faction=actor_data.get("faction"),
                                        position=Location(**actor_data["position"]) if "position" in actor_data else None,
                                        hppercent=actor_data.get("hp") * 100 // actor_data.get("maxHp")
                                        if actor_data.get("maxHp")
                                        else 0,
                                        is_frozen=True,
                                        activity=actor_data.get("activity"),
                                        order=actor_data.get("order"),
                                    )
                                    actors_list.append(actor)
                            if actors_list:
                                data["actors"] = actors_list
                        return response
                    except json.JSONDecodeError:
                        raise GameAPIError("INVALID_JSON", "服务器返回的不是有效的JSON格式")
            except (socket.timeout, ConnectionError) as exc:
                retries += 1
                if retries >= self.MAX_RETRIES:
                    raise GameAPIError("CONNECTION_ERROR", f"连接服务器失败: {exc}")
                time.sleep(self.RETRY_DELAY)
            except GameAPIError:
                raise
            except Exception as exc:
                raise GameAPIError("UNEXPECTED_ERROR", f"发生未预期的错误: {exc}")
        raise GameAPIError("CONNECTION_ERROR", "连接服务器失败")

    def _handle_response(self, response: dict, error_msg: str) -> Any:
        if response is None:
            raise GameAPIError("NO_RESPONSE", f"{error_msg}")
        return response.get("data") if "data" in response else response

    def query_actor(self, query_params: TargetsQueryParam) -> List[Actor]:
        try:
            response = self._send_request("query_actor", {"targets": query_params.to_dict()})
            result = self._handle_response(response, "查询Actor失败")
            actors: List[Actor] = []
            actors_data = result.get("actors", [])
            for data in actors_data:
                if isinstance(data, Actor):
                    actors.append(data)
                    continue
                try:
                    actor = Actor(data["id"])
                    position = Location(data["position"]["x"], data["position"]["y"])
                    hp_percent = data["hp"] * 100 // data["maxHp"] if data.get("maxHp", 0) > 0 else -1
                    actor.update_details(
                        type=data.get("type"),
                        faction=data.get("faction"),
                        position=position,
                        hppercent=hp_percent,
                        is_frozen=data.get("isFrozen", False),
                        is_dead=data.get("isDead", False),
                        activity=data.get("activity"),
                        order=data.get("order"),
                    )
                    actors.append(actor)
                except KeyError as exc:
                    raise GameAPIError("INVALID_ACTOR_DATA", f"Actor数据格式无效: {exc}")
            return actors
        except GameAPIError:
            raise
        except Exception as exc:
            raise GameAPIError("QUERY_ACTOR_ERROR", f"查询Actor时发生错误: {exc}")

    def query_map(self) -> MapQueryResult:
        try:
            response = self._send_request("map_query", {})
            data = self._handle_response(response, "查询地图失败")
            return MapQueryResult(
                MapWidth=data.get("MapWidth", 0),
                MapHeight=data.get("MapHeight", 0),
                Height=data.get("Height", [[]]),
                IsVisible=data.get("IsVisible", [[]]),
                IsExplored=data.get("IsExplored", [[]]),
                Terrain=data.get("Terrain", [[]]),
                ResourcesType=data.get("ResourcesType", [[]]),
                Resources=data.get("Resources", [[]]),
            )
        except GameAPIError:
            raise
        except Exception as exc:
            raise GameAPIError("QUERY_MAP_ERROR", f"查询地图时发生错误: {exc}")

    def map_query(self) -> MapQueryResult:
        return self.query_map()

    def player_base_info_query(self) -> PlayerBaseInfo:
        try:
            response = self._send_request("player_baseinfo_query", {})
            result = self._handle_response(response, "查询玩家基地信息失败")
            return PlayerBaseInfo(
                Cash=result.get("Cash", 0),
                Resources=result.get("Resources", 0),
                Power=result.get("Power", 0),
                PowerDrained=result.get("PowerDrained", 0),
                PowerProvided=result.get("PowerProvided", 0),
            )
        except GameAPIError:
            raise
        except Exception as exc:
            raise GameAPIError("BASE_INFO_QUERY_ERROR", f"查询玩家基地信息时发生错误: {exc}")

    def screen_info_query(self) -> ScreenInfoResult:
        try:
            response = self._send_request("screen_info_query", {})
            result = self._handle_response(response, "查询屏幕信息失败")
            return ScreenInfoResult(
                ScreenMin=Location(**result["ScreenMin"]) if "ScreenMin" in result else Location(0, 0),
                ScreenMax=Location(**result["ScreenMax"]) if "ScreenMax" in result else Location(0, 0),
                IsMouseOnScreen=result.get("IsMouseOnScreen", False),
                MousePosition=Location(**result["MousePosition"]) if "MousePosition" in result else Location(0, 0),
            )
        except GameAPIError:
            raise
        except Exception as exc:
            raise GameAPIError("SCREEN_INFO_QUERY_ERROR", f"查询屏幕信息时发生错误: {exc}")

    def fog_query(self, location: Location) -> Dict[str, bool]:
        try:
            response = self._send_request("fog_query", {"pos": location.to_dict()})
            result = self._handle_response(response, "查询迷雾信息失败")
            return {
                # 临时修复：引擎返回的 IsVisible/IsExplored 结果与实际含义相反，改回去的话把not删除即可（已修复）
                "IsVisible": bool(result.get("IsVisible", False)),
                "IsExplored": bool(result.get("IsExplored", False)),
            }
        except GameAPIError:
            raise
        except Exception as exc:
            raise GameAPIError("FOG_QUERY_ERROR", f"查询迷雾信息时发生错误: {exc}")

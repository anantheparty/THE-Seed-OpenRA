import sys
import os
import json
from typing import List

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api.game_api import GameAPI, TargetsQueryParam

def main():
    print("Connecting to OpenRA Game API...")
    api = GameAPI(host="localhost", port=7445)
    
    if not api.is_server_running():
        print("Error: Could not connect to OpenRA server.")
        return

    # 直接调用底层的 query_actorwithfrozen 或者手动调用 query_actor 并检查返回
    # 既然我们修复了 GameAPI.query_actor 的自动合并逻辑，我们可以直接调用它
    # 但为了看得更清楚，我们模拟一次直接的 request
    
    print("Checking Enemy Frozen Actors...")
    try:
        # 手动构造请求以获取原始 JSON，避开 GameAPI 的封装
        # 这样我们可以看到是否有 'frozenActors' 字段返回
        
        # 1. 敌方查询
        print("\n--- Querying '敌方' ---")
        res = api._send_request('query_actor', {
            "targets": {
                "faction": "敌方",
                "range": "all"
            }
        })
        
        if res.get("status") == 1:
            data = res.get("data", {})
            actors = data.get("actors", [])
            frozen = data.get("frozenActors", [])
            print(f"Live Actors: {len(actors)}")
            print(f"Frozen Actors: {len(frozen)}")
            
            if frozen:
                print("Sample Frozen Actor:", frozen[0])
        else:
            print("Query failed:", res)
            
        # 2. 中立查询 (再次确认)
        print("\n--- Querying '中立' ---")
        res = api._send_request('query_actor', {
            "targets": {
                "faction": "中立",
                "range": "all"
            }
        })
        
        if res.get("status") == 1:
            data = res.get("data", {})
            actors = data.get("actors", [])
            frozen = data.get("frozenActors", [])
            print(f"Live Actors: {len(actors)}")
            print(f"Frozen Actors: {len(frozen)}")
            
            # Check for mines in live actors just in case
            mine_count = 0
            for a in actors:
                # 'a' is an Actor object now, or dict if not parsed yet. 
                # Let's check type.
                # If we used raw request, it might be dict if we didn't use GameAPI wrapper method.
                # BUT wait, we used api._send_request, which calls self._receive_data then returns dict.
                # It does NOT return Actor objects. GameAPI.query_actor does that.
                # So 'actors' here is a list of DICTs from the raw json response data['actors'].
                
                # Wait, I see I used `api._send_request` in my script. 
                # _send_request returns the raw JSON dict response.
                # Inside _send_request (lines 160-200 in game_api.py), it modifies response["data"]["actors"] to be a list of Actor objects!
                # So 'actors' here ARE Actor objects.
                
                type_val = a.type if hasattr(a, 'type') else a.get('type')
                if "mine" in str(type_val).lower():
                    mine_count += 1
            print(f"Mines in Live list: {mine_count}")
            
        else:
            print("Query failed:", res)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

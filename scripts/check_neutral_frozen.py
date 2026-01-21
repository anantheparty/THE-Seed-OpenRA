import sys
import os
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

    print("Checking Neutral Frozen Actors Types...")
    try:
        # 手动构造请求
        res = api._send_request('query_actor', {
            "targets": {
                "faction": "中立",
                "range": "all"
            }
        })
        
        if res.get("status") == 1:
            data = res.get("data", {})
            frozen = data.get("frozenActors", [])
            print(f"Total Frozen Neutral Actors: {len(frozen)}")
            
            # Count types
            type_counts = {}
            for f in frozen:
                # frozen actors in raw json are dicts
                t = f.get("type", "Unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
                
                # Print detail if it's a mine
                if "mine" in str(t).lower():
                    print(f"FOUND FROZEN MINE: {f}")
            
            print("\n--- Type Distribution ---")
            for t, count in type_counts.items():
                print(f"{t}: {count}")
                
        else:
            print("Query failed:", res)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

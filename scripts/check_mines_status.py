import sys
import os
from typing import List

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api.game_api import GameAPI
from openra_api.intel.intelligence_service import IntelligenceService
from agents.global_blackboard import GlobalBlackboard

def main():
    print("Connecting to OpenRA Game API...")
    api = GameAPI(host="localhost", port=7445)
    
    if not api.is_server_running():
        print("Error: Could not connect to OpenRA server.")
        return

    bb = GlobalBlackboard()
    intel = IntelligenceService(api, bb)
    
    print("Querying game state (including map and actors)...")
    try:
        # 强制更新
        raw_state = intel._query_game_state(query_map=True)
        
        mines = []
        mine_keywords = {"mine", "gmine"}
        
        print(f"\n--- Checking {len(raw_state.all_actors)} total actors ---")
        
        for actor in raw_state.all_actors:
            # Check if it's a mine
            if actor.type and any(k in str(actor.type).lower() for k in mine_keywords):
                mines.append(actor)
                
        print(f"Found {len(mines)} mine actors.")
        print("\n--- Mine Details ---")
        print(f"{'ID':<10} {'Type':<10} {'Pos':<15} {'Faction':<10} {'Frozen':<10}")
        print("-" * 60)
        
        frozen_count = 0
        for m in mines:
            pos_str = f"({m.position.x}, {m.position.y})" if m.position else "None"
            print(f"{m.id:<10} {m.type:<10} {pos_str:<15} {m.faction:<10} {m.is_frozen:<10}")
            if m.is_frozen:
                frozen_count += 1
                
        print("-" * 60)
        print(f"Total Frozen Mines: {frozen_count}")
        
        if len(mines) > 0 and frozen_count == 0:
            print("\nNOTE: No frozen mines detected. This might be because:")
            print("1. All mines are currently visible.")
            print("2. 'frozenActors' are not being returned for Neutral faction.")
            print("3. Or 'is_frozen' flag is not being set correctly.")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

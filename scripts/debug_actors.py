import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from openra_api.game_api import GameAPI
from openra_api.models import TargetsQueryParam

def debug_actors():
    api = GameAPI("localhost", 7445)
    
    print("Querying all actors...")
    factions = ["己方", "敌方", "友方", "中立"]
    
    all_actors = []
    for f in factions:
        try:
            print(f"Querying faction: {f}...")
            # Query all actors for the faction
            # Note: Using TargetsQueryParam as per GameAPI requirement
            actors = api.query_actor(TargetsQueryParam(faction=f, range="all"))
            if actors:
                print(f"  Found {len(actors)} actors.")
                for a in actors:
                    # Enrich with faction for our debug output
                    a.faction = f 
                    all_actors.append(a)
            else:
                print("  No actors found.")
        except Exception as e:
            print(f"  Error querying {f}: {e}")

    print("\n--- Detailed Actor Dump ---")
    print(f"{'ID':<10} {'Type':<25} {'Faction':<10} {'Pos':<15} {'Frozen':<8} {'HP':<10}")
    print("-" * 80)
    
    for a in all_actors:
        pos_str = f"{a.position.x},{a.position.y}" if a.position else "None"
        # Only print buildings or key units to reduce noise
        # Filter for common building types or just print everything if list is short
        # Let's print everything for now but highlight potential bases
        
        type_str = str(a.type)
        is_base = "fact" in type_str.lower() or "construction" in type_str.lower() or "base" in type_str.lower() or "建造" in type_str
        
        prefix = ">>> " if is_base else "    "
        
        print(f"{prefix}{a.id:<6} {type_str:<25} {a.faction:<10} {pos_str:<15} {str(a.is_frozen):<8} {a.hppercent}")

if __name__ == "__main__":
    debug_actors()

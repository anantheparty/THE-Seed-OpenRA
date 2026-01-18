#!/usr/bin/env python3
"""Quick test to check if OpenRA API is accessible."""
from openra_api.game_api import GameAPI

def test_connection():
    try:
        api = GameAPI(host="localhost", port=7445, language="zh")
        print("✓ GameAPI initialized")

        # Try to get basic info
        try:
            map_info = api.map_query()
            print(f"✓ Map info retrieved (size: {map_info.MapWidth}x{map_info.MapHeight})")
        except Exception as e:
            print(f"  ⚠ Map query failed: {e}")

        try:
            screen_info = api.screen_info_query()
            print(f"✓ Screen info: {screen_info}")
        except Exception as e:
            print(f"  ⚠ Screen info query failed: {e}")

        try:
            player_info = api.player_base_info_query()
            print(f"✓ Player info: faction={player_info.Faction}, color={player_info.Color}")
        except Exception as e:
            print(f"  ⚠ Player info query failed: {e}")

        try:
            match_info = api.match_info_query()
            print(f"✓ Match info: {match_info}")
        except Exception as e:
            print(f"  ⚠ Match info query failed: {e}")

        print("\n✓ Connection to OpenRA API successful!")
        return True
    except Exception as e:
        print(f"✗ Error connecting to OpenRA: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_connection()
    exit(0 if success else 1)

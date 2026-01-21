
import sys
import os

# Add parent directory to path to import openra_api
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api import GameAPI, GameAPIError

def main():
    print("Connecting to GameAPI at localhost:7445...")
    api = GameAPI("localhost", 7445)
    
    try:
        print("Fetching Map Data...")
        map_data = api.map_query()
        
        width = map_data.MapWidth
        height = map_data.MapHeight
        print(f"Map Dimensions from API: Width={width}, Height={height}")
        
        resources = map_data.Resources
        
        # Determine actual dimensions of the list of lists
        # Python list of lists: outer list length is the first dimension
        dim1 = len(resources)
        dim2 = len(resources[0]) if dim1 > 0 else 0
        
        print(f"Resources Array Structure: len(outer)={dim1}, len(inner)={dim2}")
        
        print("\n--- VERIFICATION ---")
        if dim1 == width and dim2 == height:
            print("MATCH: len(outer) == Width. This implies [x][y] (Column-Major) structure.")
            print("  resources[x] selects a COLUMN.")
            print("  resources[x][y] selects a CELL at (x, y).")
        elif dim1 == height and dim2 == width:
            print("MATCH: len(outer) == Height. This implies [y][x] (Row-Major) structure.")
            print("  resources[y] selects a ROW.")
            print("  resources[y][x] selects a CELL at (x, y).")
        else:
            print("MISMATCH: Array dimensions do not match Map dimensions in either orientation.")
            print(f"  Width={width}, Height={height}")
            print(f"  Dim1={dim1}, Dim2={dim2}")
            
        # Additional edge check to be sure
        print("\n--- EDGE CHECK ---")
        try:
            # Try to access [Width-1][Height-1]
            val = resources[width-1][height-1]
            print(f"Accessing resources[Width-1][Height-1] (resources[{width-1}][{height-1}]): SUCCESS")
        except IndexError:
            print(f"Accessing resources[Width-1][Height-1] (resources[{width-1}][{height-1}]): FAILED (IndexError)")

        try:
            # Try to access [Height-1][Width-1]
            val = resources[height-1][width-1]
            print(f"Accessing resources[Height-1][Width-1] (resources[{height-1}][{width-1}]): SUCCESS")
        except IndexError:
            print(f"Accessing resources[Height-1][Width-1] (resources[{height-1}][{width-1}]): FAILED (IndexError)")

    except GameAPIError as e:
        print(f"GameAPI Error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()

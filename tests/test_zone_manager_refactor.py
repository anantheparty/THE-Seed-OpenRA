import unittest
from unittest.mock import MagicMock
from openra_api.models import Location, Actor
from openra_api.intel.zone_manager import ZoneManager, ZoneInfo
from openra_api.intel.clustering import SpatialClustering

class TestZoneManagerRefactor(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.blackboard = MagicMock()
        self.blackboard.map_data.width = 100
        self.blackboard.map_data.height = 100
        
        self.zone_manager = ZoneManager()
        self.zone_manager.screen_width = 20

    def test_clustering_basic(self):
        # Create two distinct clusters of points
        # Cluster 1: around (10, 10)
        c1 = [Location(10+dx, 10+dy) for dx in range(3) for dy in range(3)]
        # Cluster 2: around (50, 50)
        c2 = [Location(50+dx, 50+dy) for dx in range(3) for dy in range(3)]
        
        points = c1 + c2
        
        # We need to mock _get_resource_points because it calls map_query
        # Or we can test the clustering logic directly via SpatialClustering
        # But we want to test ZoneManager's integration
        
        # Let's mock _find_resource_clusters input or test internal method if possible
        # ZoneManager._find_resource_clusters is internal.
        # But we can call it directly for testing.
        
        # Mock MapQueryResult is hard, let's test SpatialClustering first to ensure util is good
        clusters = SpatialClustering.dbscan_grid(points, eps=4.0, min_samples=2)
        self.assertEqual(len(clusters), 2)

    def test_kmeans_split(self):
        # Create a long cluster that exceeds screen width (20)
        # Points from x=0 to x=40
        points = [Location(x, 10) for x in range(0, 41, 2)] # 0, 2, ..., 40. Width 40.
        
        # Verify bounding box
        min_x = min(p.x for p in points)
        max_x = max(p.x for p in points)
        self.assertEqual(max_x - min_x, 40)
        
        # Test split
        # We expect 2 or more clusters depending on k logic in ZoneManager
        # ZoneManager uses: width > screen_width * 0.8 (16). 40 > 16.
        # It calculates k = ceil(width / (screen_width * 0.8)) = ceil(40/16) = 3
        
        clusters = SpatialClustering.kmeans_split(points, k=3)
        self.assertEqual(len(clusters), 3)

    def test_topology_building(self):
        # Create 3 zones in a line: A(10,10) - B(20,20) - C(100,100)
        # A and B are close (dist ~14), B and C are far (dist ~113)
        # Using Gabriel Graph:
        # A(10,10), B(20,20), C(100,100)
        # Edge AB: Midpoint (15,15), radius ~7. C is far away. d(C, mid) > r. Valid.
        # Edge BC: Midpoint (60,60), radius ~56. A is at (10,10). d(A, mid) = sqrt(50^2+50^2) ~70 > 56. Valid.
        # Edge AC: Midpoint (55,55), radius ~63. B is at (20,20). d(B, mid) = sqrt(35^2+35^2) ~49 < 63.
        # B is inside circle of AC. So AC should NOT be connected.
        
        z1 = ZoneInfo(id=1, center=Location(10, 10), bounding_box=(0,0,20,20), resource_value=100, type="RESOURCE")
        z2 = ZoneInfo(id=2, center=Location(20, 20), bounding_box=(10,10,30,30), resource_value=100, type="RESOURCE")
        z3 = ZoneInfo(id=3, center=Location(100, 100), bounding_box=(90,90,110,110), resource_value=100, type="RESOURCE")
        
        self.zone_manager.zones = {1: z1, 2: z2, 3: z3}
        self.zone_manager._build_topology()
        
        # Check neighbors
        self.assertIn(2, z1.neighbors) # A-B connected
        self.assertIn(1, z2.neighbors)
        self.assertIn(3, z2.neighbors) # B-C connected
        self.assertIn(2, z3.neighbors)
        
        self.assertNotIn(3, z1.neighbors) # A-C blocked by B
        self.assertNotIn(1, z3.neighbors)

    def test_pathfinding(self):
        # Create a chain: 1-2-3
        z1 = ZoneInfo(id=1, center=Location(0, 0), bounding_box=(0,0,10,10), resource_value=100, type="RESOURCE")
        z2 = ZoneInfo(id=2, center=Location(30, 0), bounding_box=(25,0,35,10), resource_value=100, type="RESOURCE") # dist 30
        z3 = ZoneInfo(id=3, center=Location(60, 0), bounding_box=(55,0,65,10), resource_value=100, type="RESOURCE") # dist 30 from z2
        
        # Set neighbors manually to ensure topology is as expected for pathfinding test
        z1.neighbors = [2]
        z2.neighbors = [1, 3]
        z3.neighbors = [2]
        
        self.zone_manager.zones = {1: z1, 2: z2, 3: z3}
        
        path = self.zone_manager.find_path(1, 3)
        self.assertEqual(path, [1, 2, 3])
        
        path_reverse = self.zone_manager.find_path(3, 1)
        self.assertEqual(path_reverse, [3, 2, 1])
        
        # No path
        z4 = ZoneInfo(id=4, center=Location(100, 100), bounding_box=(90,90,110,110), resource_value=100, type="RESOURCE")
        self.zone_manager.zones[4] = z4
        path_none = self.zone_manager.find_path(1, 4)
        self.assertEqual(path_none, [])

    def test_update_bases_friendly(self):
        # Test friendly base identification
        z1 = ZoneInfo(id=1, center=Location(0, 0), bounding_box=(0,0,10,10), type="RESOURCE")
        self.zone_manager.zones = {1: z1}
        
        # Mock Actor
        # actor_id: int, type: str, faction: str, position: Location, hppercent: int
        # Actor constructor: Actor(actor_id)
        # update_details: type, faction, position, hppercent, activity, order
        
        a1 = Actor(101)
        a1.update_details("fact", "Soviet", Location(5, 5), 100)
        
        a2 = Actor(102)
        a2.update_details("powr", "Soviet", Location(6, 6), 100)
        
        all_units = [a1, a2]
        
        # Test 1: Identify as Friendly (My Faction)
        self.zone_manager.update_bases(all_units, my_faction="Soviet")
        self.assertEqual(z1.type, "MAIN_BASE")
        self.assertEqual(z1.owner_faction, "Soviet")
        self.assertTrue(z1.is_friendly)
        
        # Test 2: Identify as Friendly (Ally Faction)
        z1.type = "RESOURCE" # Reset
        z1.is_friendly = False
        self.zone_manager.update_bases(all_units, my_faction="Allies", ally_factions=["Soviet"])
        self.assertEqual(z1.type, "MAIN_BASE")
        self.assertEqual(z1.owner_faction, "Soviet") # Still Soviet owned
        self.assertTrue(z1.is_friendly) # But friendly
        
        # Test 3: Enemy
        z1.type = "RESOURCE" # Reset
        z1.is_friendly = False
        self.zone_manager.update_bases(all_units, my_faction="Allies", ally_factions=["France"])
        self.assertEqual(z1.type, "MAIN_BASE")
        self.assertEqual(z1.owner_faction, "Soviet")
        self.assertFalse(z1.is_friendly)

    def test_mine_topology(self):
        # Test creating zones from mines directly
        # Mock map data with resources
        self.blackboard.map_data.MapWidth = 100
        self.blackboard.map_data.MapHeight = 100
        
        # Create resources around (10,10) and (50,50)
        resources = [[0]*100 for _ in range(100)]
        for dx in range(5):
            for dy in range(5):
                resources[10+dy][10+dx] = 10
                resources[50+dy][50+dx] = 10
        self.blackboard.map_data.Resources = resources
        
        # Mock Mine Actors
        m1 = Actor(201)
        m1.update_details("mine", "Neutral", Location(12, 12), 100)
        
        m2 = Actor(202)
        m2.update_details("mine", "Neutral", Location(52, 52), 100)
        
        mine_actors = [m1, m2]
        
        self.zone_manager.update_from_map_query(self.blackboard.map_data, mine_actors)
        
        # DBSCAN + Snap Logic:
        # DBSCAN will find 2 clusters.
        # Mine actors should be found within them and snap centers.
        
        self.assertEqual(len(self.zone_manager.zones), 2)
        
        # Verify centers are exactly mine positions
        centers = {z.center.to_dict()['x']: z.center.to_dict()['y'] for z in self.zone_manager.zones.values()}
        self.assertIn(12, centers) # x=12 exists
        self.assertEqual(centers[12], 12) # y=12
        
        self.assertIn(52, centers)
        self.assertEqual(centers[52], 52)
        
        # Verify resource values > 0
        for z in self.zone_manager.zones.values():
            self.assertGreater(z.resource_value, 0)

if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import MagicMock
from agents.global_blackboard import GlobalBlackboard, Signal
from agents.base_agent import BaseAgent

class TestSignalCommunication(unittest.TestCase):
    def setUp(self):
        self.global_bb = GlobalBlackboard()
        self.mock_game_api = MagicMock()
        self.mock_mid_layer = MagicMock()
        
        # Mocking NodeFactory and FSM inside BaseAgent is tricky without full mocking
        # So we might just test the signal flow if we can instantiate BaseAgent partially
        # or just test the methods if we mock BaseAgent's dependencies.
        
        # However, BaseAgent.__init__ creates FSM, which might be fine if dependencies are mocked.
        # But FSM creates Nodes...
        pass

    def test_global_blackboard_signal_flow(self):
        bb = GlobalBlackboard()
        
        # Publish
        sig1 = Signal(sender="AgentA", receiver="AgentB", type="PING", payload="123")
        bb.publish_signal(sig1)
        
        # Consume
        signals_b = bb.consume_signals("AgentB")
        self.assertEqual(len(signals_b), 1)
        self.assertEqual(signals_b[0].payload, "123")
        
        # Consume for unrelated agent
        signals_c = bb.consume_signals("AgentC")
        self.assertEqual(len(signals_c), 0)
        
        # Broadcast
        sig2 = Signal(sender="AgentA", receiver="all", type="BROADCAST", payload="HELLO")
        bb.publish_signal(sig2)
        
        signals_b_2 = bb.consume_signals("AgentB")
        # Should get broadcast + previous one if not consumed? 
        # Wait, consume_signals currently does NOT remove signals from market.
        # It just filters.
        # So signals_b_2 should have PING and BROADCAST.
        self.assertEqual(len(signals_b_2), 2)
        
        signals_c_2 = bb.consume_signals("AgentC")
        self.assertEqual(len(signals_c_2), 1)
        self.assertEqual(signals_c_2[0].type, "BROADCAST")

    def test_ttl_cleanup(self):
        bb = GlobalBlackboard()
        sig = Signal(sender="A", receiver="B", type="T", payload="", ttl=1)
        bb.publish_signal(sig)
        
        bb.clear_expired_signals() # ttl becomes 0
        self.assertEqual(len(bb.market), 1) # Still there because code says:
        # self.market = [s for s in self.market if s.ttl > 0]
        # s.ttl -= 1 (This happens AFTER filtering in my implementation? Let's check code)
        
        # Code:
        # self.market = [s for s in self.market if s.ttl > 0]
        # for s in self.market: s.ttl -= 1
        
        # So if TTL=1:
        # Filter: 1 > 0 -> Keep.
        # Decrement: 1 -> 0.
        # Next call:
        # Filter: 0 > 0 -> Remove.
        
        bb.clear_expired_signals() # 2nd call
        self.assertEqual(len(bb.market), 0)

if __name__ == '__main__':
    unittest.main()

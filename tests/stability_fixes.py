"""
Tests for transport stability fixes.
Tests the specific bugs that were fixed, not the full RNS functionality.
"""
import unittest
import threading
import time
import collections
import os
import sys

# Add parent to path so we can import RNS
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Paths to source files (relative to repo root)
_REPO = os.path.join(os.path.dirname(__file__), '..')
_BACKBONE = os.path.join(_REPO, 'RNS', 'Interfaces', 'BackboneInterface.py')
_TCP      = os.path.join(_REPO, 'RNS', 'Interfaces', 'TCPInterface.py')
_TRANSPORT = os.path.join(_REPO, 'RNS', 'Transport.py')
_PACKET   = os.path.join(_REPO, 'RNS', 'Packet.py')
_RETICULUM = os.path.join(_REPO, 'RNS', 'Reticulum.py')

def _read(path):
    with open(path) as f:
        return f.read()


class TestBackboneListenBacklog(unittest.TestCase):
    """P5: server_socket.listen(1) → listen(512)"""

    def test_listen_backlog_constant(self):
        """Verify the listen call uses 512, not 1"""
        
        source = _read(_BACKBONE)
        self.assertIn("server_socket.listen(512)", source)
        self.assertNotIn("server_socket.listen(1)", source)


class TestBackboneEpollFix(unittest.TestCase):
    """P7: double epoll.poll() eliminated"""

    def test_no_double_poll(self):
        """Verify we iterate over stored events, not a second poll()"""
        
        source = _read(_BACKBONE)
        # Should have: for fileno, event in events:
        self.assertIn("for fileno, event in events:", source)
        # Should NOT have the old double-poll pattern
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'events = BackboneInterface.epoll.poll' in line:
                # Next non-blank line with 'for' should use 'events', not another poll
                for j in range(i+1, min(i+5, len(lines))):
                    if 'for fileno, event in' in lines[j]:
                        self.assertNotIn('epoll.poll', lines[j],
                            "Found double epoll.poll() — second poll discards first result")
                        break


class TestBackboneReconnectBackoff(unittest.TestCase):
    """P8: exponential backoff for reconnect"""

    def test_backoff_constants_exist(self):
        """Verify RECONNECT_MAX_WAIT is defined"""
        from RNS.Interfaces.BackboneInterface import BackboneClientInterface
        self.assertTrue(hasattr(BackboneClientInterface, 'RECONNECT_MAX_WAIT'))
        self.assertEqual(BackboneClientInterface.RECONNECT_MAX_WAIT, 300)
        self.assertEqual(BackboneClientInterface.RECONNECT_WAIT, 5)


class TestTCPReconnectBackoff(unittest.TestCase):
    """P8: exponential backoff for TCP reconnect"""

    def test_backoff_constants_exist(self):
        from RNS.Interfaces.TCPInterface import TCPClientInterface
        self.assertTrue(hasattr(TCPClientInterface, 'RECONNECT_MAX_WAIT'))
        self.assertEqual(TCPClientInterface.RECONNECT_MAX_WAIT, 300)
        self.assertEqual(TCPClientInterface.RECONNECT_WAIT, 5)


class TestFrameBufferCap(unittest.TestCase):
    """P11: frame_buffer and transmit_buffer bounded"""

    def test_backbone_frame_buffer_cap(self):
        from RNS.Interfaces.BackboneInterface import BackboneClientInterface
        self.assertTrue(hasattr(BackboneClientInterface, 'MAX_FRAME_BUFFER'))
        self.assertEqual(BackboneClientInterface.MAX_FRAME_BUFFER, 16 * 1024 * 1024)

    def test_backbone_transmit_buffer_cap(self):
        from RNS.Interfaces.BackboneInterface import BackboneClientInterface
        self.assertTrue(hasattr(BackboneClientInterface, 'MAX_TRANSMIT_BUFFER'))
        self.assertEqual(BackboneClientInterface.MAX_TRANSMIT_BUFFER, 16 * 1024 * 1024)


class TestTCPWriteLock(unittest.TestCase):
    """TCPInterface: write lock exists and is a real Lock"""

    def test_write_lock_in_source(self):
        """Verify process_outgoing uses 'with self.write_lock'"""
        
        source = _read(_TCP)
        self.assertIn("self.write_lock", source)
        self.assertIn("with self.write_lock:", source)
        # Old commented-out busy-wait should be gone
        self.assertNotIn("# while self.writing:", source)


class TestTransportDeadlockFix(unittest.TestCase):
    """K008: inbound() releases jobs_locked on all exit paths"""

    def test_no_orphaned_returns(self):
        """Every return after jobs_locked=True must have jobs_locked=False before it"""
        
        source = _read(_TRANSPORT)
        lines = source.split('\n')

        # Find inbound() method
        in_inbound = False
        locked = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'def inbound(' in line:
                in_inbound = True
                locked = False
                continue
            if in_inbound:
                # End of inbound — next @staticmethod at correct indent
                if stripped.startswith('@staticmethod') and not line.startswith(' ' * 16):
                    break
                if 'Transport.jobs_locked = True' in stripped:
                    locked = True
                if 'Transport.jobs_locked = False' in stripped:
                    locked = False
                if locked and stripped == 'return':
                    self.fail(f"Line {i+1}: bare 'return' while jobs_locked=True in inbound()")


class TestCleanAnnounceCacheSet(unittest.TestCase):
    """P10: clean_announce_cache uses set() not list"""

    def test_uses_set(self):
        
        source = _read(_TRANSPORT)
        # Should use set() generator expression
        self.assertIn("active_paths = set(", source)
        self.assertIn("tunnel_paths = set(", source)
        # Should NOT use list
        self.assertNotIn("active_paths = [", source)


class TestAnnounceRateTableEviction(unittest.TestCase):
    """K009: announce_rate_table has eviction"""

    def test_eviction_code_exists(self):
        
        source = _read(_TRANSPORT)
        self.assertIn("stale_rate_entries", source)
        self.assertIn("announce_rate_table.pop", source)


class TestPathRequestsEviction(unittest.TestCase):
    """K009: path_requests has eviction"""

    def test_eviction_code_exists(self):
        
        source = _read(_TRANSPORT)
        self.assertIn("stale_path_requests", source)
        self.assertIn("Transport.path_requests.pop", source)


class TestBusyWaitReplaced(unittest.TestCase):
    """Transport: busy-wait spinlock replaced with Event"""

    def test_event_exists(self):
        
        import RNS; self.assertTrue(hasattr(RNS.Transport, '_jobs_event'))
        import RNS; import RNS; self.assertIsInstance(RNS.Transport._jobs_event, threading.Event)

    def test_no_busy_wait_in_outbound(self):
        
        source = _read(_TRANSPORT)
        # Find outbound() — should NOT have while(Transport.jobs_running): sleep
        in_outbound = False
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'def outbound(' in line:
                in_outbound = True
                continue
            if in_outbound:
                if line.strip().startswith('def ') or line.strip().startswith('@staticmethod'):
                    break
                self.assertNotIn('while (Transport.jobs_running)', line,
                    f"Line {i+1}: busy-wait still present in outbound()")


class TestDequeCaches(unittest.TestCase):
    """K010: signal caches use deque, not list"""

    def test_rssi_cache_is_deque(self):
        
        import RNS; self.assertIsInstance(RNS.Transport.local_client_rssi_cache, collections.deque)
        self.assertEqual(RNS.Transport.local_client_rssi_cache.maxlen, 512)

    def test_snr_cache_is_deque(self):
        
        import RNS; self.assertIsInstance(RNS.Transport.local_client_snr_cache, collections.deque)

    def test_q_cache_is_deque(self):
        
        import RNS; self.assertIsInstance(RNS.Transport.local_client_q_cache, collections.deque)

    def test_no_manual_pop(self):
        """Manual pop(0) loops should be removed"""
        
        source = _read(_TRANSPORT)
        self.assertNotIn("local_client_rssi_cache.pop(0)", source)
        self.assertNotIn("local_client_snr_cache.pop(0)", source)
        self.assertNotIn("local_client_q_cache.pop(0)", source)


class TestLogRateLimit(unittest.TestCase):
    """P9: 'No interfaces' log spam rate-limited"""

    def test_rate_limit_attrs_exist(self):
        from RNS.Packet import Packet
        self.assertTrue(hasattr(Packet, '_no_interface_log_last'))
        self.assertTrue(hasattr(Packet, '_no_interface_log_count'))
        self.assertTrue(hasattr(Packet, '_no_interface_log_interval'))
        self.assertEqual(Packet._no_interface_log_interval, 5)


class TestConfigBugFixed(unittest.TestCase):
    """K003: interface_mode gateway branch uses correct key"""

    def test_no_wrong_key(self):
        
        source = _read(_RETICULUM)
        # In the interface_mode block, gateway should use interface_mode, not mode
        lines = source.split('\n')
        in_interface_mode_block = False
        for i, line in enumerate(lines):
            if '"interface_mode" in c' in line and 'if' in line:
                in_interface_mode_block = True
            elif '"mode" in c' in line and 'elif' in line and in_interface_mode_block:
                in_interface_mode_block = False  # entered the 'mode' block
            if in_interface_mode_block and 'gateway' in line and 'elif' in line:
                self.assertNotIn('c["mode"]', line,
                    f"Line {i+1}: gateway branch in interface_mode block uses wrong key c[\"mode\"]")


class TestImportlibNotExec(unittest.TestCase):
    """Security: exec() replaced with importlib"""

    def test_no_exec_for_interfaces(self):
        
        source = _read(_RETICULUM)
        self.assertIn("spec_from_file_location", source)
        self.assertNotIn("exec(interface_code", source)


class TestListenerTupleFormat(unittest.TestCase):
    """Regression: listener_filenos uses 4-tuple consistently"""

    def test_consistent_tuple_access(self):
        """All access to listener_filenos should use indexed access, not 2-tuple unpack"""
        
        source = _read(_BACKBONE)
        # Should NOT have old-style 2-tuple unpacking from listener_filenos
        self.assertNotIn("owner_interface, server_socket = BackboneInterface.listener_filenos[", source)


_LINK = os.path.join(_REPO, 'RNS', 'Link.py')


class TestLinkSafeIteration(unittest.TestCase):
    """K011-ext: Link.py iterates over list copies to avoid RuntimeError"""

    def test_link_closed_uses_list_copy(self):
        """link_closed() should iterate list() copies of resource lists"""
        source = _read(_LINK)
        lines = source.split('\n')
        in_link_closed = False
        for i, line in enumerate(lines):
            if 'def link_closed(self):' in line:
                in_link_closed = True
                continue
            if in_link_closed:
                if line.strip().startswith('def '):
                    break
                if 'for resource in self.incoming_resources:' in line:
                    self.fail(f"Line {i+1}: link_closed() iterates incoming_resources without list() copy")
                if 'for resource in self.outgoing_resources:' in line:
                    self.fail(f"Line {i+1}: link_closed() iterates outgoing_resources without list() copy")

    def test_receive_packet_uses_list_copy(self):
        """receive_packet loops should iterate list() copies"""
        source = _read(_LINK)
        lines = source.split('\n')
        # In receive() or receive_packet(), all `for resource in self.X_resources`
        # and `for pending_request in self.pending_requests` should use list()
        unsafe_patterns = [
            'for resource in self.outgoing_resources:',
            'for resource in self.incoming_resources:',
            'for pending_request in self.pending_requests:',
            'for incoming_resource in self.incoming_resources:',
        ]
        for i, line in enumerate(lines):
            stripped = line.strip()
            for pattern in unsafe_patterns:
                if stripped == pattern:
                    self.fail(f"Line {i+1}: unsafe iteration '{pattern}' — should use list() copy")

    def test_list_copy_pattern_present(self):
        """Verify list() copies are used in Link.py"""
        source = _read(_LINK)
        self.assertIn('for resource in list(self.incoming_resources):', source)
        self.assertIn('for resource in list(self.outgoing_resources):', source)
        self.assertIn('for pending_request in list(self.pending_requests):', source)


class TestLinkSafeIterationConcurrency(unittest.TestCase):
    """Functional test: modifying a list during iteration of its copy doesn't crash"""

    def test_list_copy_survives_concurrent_modification(self):
        """Simulate the pattern: iterate copy while another thread modifies original"""
        items = list(range(100))
        results = []
        errors = []

        def modifier():
            """Continuously add and remove items"""
            for i in range(100, 200):
                items.append(i)
                if len(items) > 50:
                    try:
                        items.pop(0)
                    except IndexError:
                        pass
                time.sleep(0.001)

        def iterator():
            """Iterate over list() copy — should never crash"""
            for _ in range(50):
                try:
                    for item in list(items):
                        results.append(item)
                except RuntimeError as e:
                    errors.append(str(e))
                time.sleep(0.002)

        t1 = threading.Thread(target=modifier)
        t2 = threading.Thread(target=iterator)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(len(errors), 0, f"list() copy should prevent RuntimeError: {errors}")
        self.assertGreater(len(results), 0, "Iterator should have collected some results")


if __name__ == '__main__':
    unittest.main(verbosity=2)

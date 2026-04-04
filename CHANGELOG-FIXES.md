# Changelog — Transport Stability Fixes
All fixes target the upstream Reticulum 1.1.4 codebase.
Branch: `fixes/transport-stability`


#### [`85f4c8f`](../../commit/85f4c8f) **[HIGH]** Aggressive stale path cleanup — 48h TTL and hop sanity limit.
Stale paths with dead next-hop nodes cause "Could not establish link" — link request packets are sent into dead routes and timeout.

**Fix:** (1) `DESTINATION_TIMEOUT` reduced from 7 days to 48 hours — paths not refreshed by a new announce are pruned faster. (2) Hop count sanity check during `jobs()` cleanup — paths with more than 64 hops (half of `PATHFINDER_M=128`) are removed. In production, 115-hop and 24-hop routes were observed polluting the path table.

---

#### [`aed567e`](../../commit/aed567e) **[CRITICAL]** Revert transmit_buffer to bytes — bytearray conflicts with epoll non-blocking send.
`bytearray` transmit_buffer caused two crashes: (1) `send()` holds a buffer reference, concurrent `.extend()` raises "Existing exports of data: object cannot be re-sized". (2) `del buf[:written]` fails when buffer is `bytes`. Both killed the epoll I/O loop via `finally: _job_active = False`, making all spawned client interfaces deaf.

**Fix:** Reverted `transmit_buffer` to immutable `bytes` (safe for concurrent send + append). `frame_buffer` stays `bytearray` (receive path has no concurrent access). Escape-once fan-out cache retained.

---

#### [`49c8643`](../../commit/49c8643) **[CRITICAL]** Fix two regressions — missing `now` variable and bytes/bytearray crash in epoll send.
Two bugs from our own commits: (1) `now` variable not defined in rate_entry eviction scope (from commit 93b7273) — `NameError` every 5 seconds breaking ALL Transport jobs. (2) `del bytearray[:n]` on `transmit_buffer` type mismatch. Root cause of "Could not find path to destination" — server accepted clients but sent them zero data.

**Fix:** (1) Added `now = time.time()` before eviction loop. (2) Added `isinstance` check with bytearray fast path and bytes fallback.

---

#### [`8a69291`](../../commit/8a69291) **[HIGH]** IFAC mask O(n²)→bytearray, escape-once fan-out cache, tunnel_table Lock+atomic.
Three CPU bottlenecks: (1) `Transport.transmit()` IFAC mask uses per-byte `bytes` concatenation — O(n²) on every outgoing packet. (2) `HDLC.escape()` called separately for each of N clients — redundant work. (3) `save_tunnel_table()` uses busy-wait boolean.

**Fix:** (1) IFAC mask → `bytearray` index assignment. (2) Escape-once fan-out cache by `id(data)` — with 100 clients, eliminates 99 redundant escape operations per packet. (3) `threading.Lock(timeout=5)` + atomic `os.replace()`.

---

#### [`ad6f2b9`](../../commit/ad6f2b9) **[MEDIUM]** Replace busy-wait booleans with threading.Lock in save_packet_hashlist and save_path_table.
Same pattern as `save_known_destinations` (c9d0d41): boolean flag with `sleep(0.2)` polling loop.

**Fix:** `threading.Lock(timeout=5)`. Both functions also now write to `.tmp` then `os.replace()` for atomic file updates. Lock released in `finally` block.

---

#### [`cd681fd`](../../commit/cd681fd) **[HIGH]** Reduce packet_hashlist maxsize from 1M to 128K — saves ~168MB RAM.
`hashlist_maxsize` was 1,000,000 — two sets of 500K entries each consumed ~193MB. Packet dedup only needs seconds of history (duplicate packets arrive immediately, not hours later).

**Fix:** Reduced to 128K (64K per set, ~25MB total). Swap frequency increases from every ~2.4 hours to every ~18 minutes on a busy node — acceptable tradeoff for 168MB savings.

---

#### [`101de36`](../../commit/101de36) **[HIGH]** spawned_interfaces O(n²)→O(1), receipts/timestamps→deque, path_table LRU cap.
Three data structure issues: (1) `spawned_interfaces` uses `while x in list: list.remove(x)` — O(n²) on disconnect with 100+ clients. (2) `Transport.receipts.pop(0)` is O(n). (3) `path_table` unbounded — ~270 entries/hour, 45K+ in a week.

**Fix:** (1) `spawned_interfaces` list → dict keyed by `id(interface)`. (2) `receipts` → `collections.deque`; `rate_entry["timestamps"]` → `deque(maxlen=16)`. (3) `MAX_PATH_TABLE = 16384` with LRU eviction by oldest timestamp.

---

#### [`36e8ba6`](../../commit/36e8ba6) **[CRITICAL]** Replace O(n²) bytes operations with bytearray across entire packet pipeline.
Production py-spy profiling showed `process_outgoing` consuming 88% CPU on a node with 100+ clients. Root cause: every buffer operation uses immutable `bytes` — each `+=`, `replace()`, and slice creates a full copy. IFAC unmask in `Transport.inbound()` was worst: per-byte `bytes([b ^ mask[i]])` concatenation in a loop — O(n²) on every inbound packet.

**Fix:** IFAC unmask → `bytearray` with in-place index XOR assignment. `frame_buffer` → `bytearray` with `.extend()` and `del buf[:n]`. HDLC constants pre-computed as module-level bytes across BackboneInterface, TCPInterface, LocalInterface.

---

#### [`1d74b3b`](../../commit/1d74b3b) **[MONITORING]** Add cumulative client connection counter to exporter.
`rns_interface_clients` is a gauge (current connections only). No way to see historical connection trends.

**Fix:** Added `rns_interface_clients_total` counter that tracks cumulative connections by detecting client count increases between collection cycles. Added "Connected Clients (history)" timeseries panel to Grafana dashboard.

---

#### [`c9d0d41`](../../commit/c9d0d41) **[HIGH]** Replace busy-wait boolean with Lock and atomic write in save_known_destinations.
`Identity.save_known_destinations()` used a boolean flag with busy-wait polling (`sleep(0.2)` loop) instead of a proper lock. Also wrote directly to the destination file — a crash mid-write corrupts it.

**Fix:** boolean → `threading.Lock` with 5s timeout. Write to `.tmp` then `os.replace()` for atomic update. Lock released in `finally` block.

---

#### [`ca1fadd`](../../commit/ca1fadd) **[MONITORING]** Add Prometheus metrics exporter for transport nodes.
No monitoring existed for Reticulum transport nodes. Operators had zero visibility into memory growth, table sizes, interface health, or announce storms.

**Fix:** `tools/rns_exporter.py` — connects to rnsd via shared instance RPC, exposes `/metrics`. Scalar metrics: uptime, interfaces, rx/tx, RSS, path table size, link count, announce rate table size, blackholed count. Per-interface: online, traffic, clients, announce queue, held announces, incoming/outgoing announce frequency.

---

#### [`56fad89`](../../commit/56fad89) **[MEDIUM]** Safe iteration over Transport.interfaces across codebase.
`Transport.interfaces` is modified when interfaces are added at startup or removed on teardown (network error callbacks). 12 iteration sites in Transport.py and 1 in Reticulum.py iterate without protection.

**Fix:** `list()` copies for all 13 iteration sites.

---

#### [`72c7fca`](../../commit/72c7fca) **[MEDIUM]** Safe list iteration in Discovery.py monitor job.
`__monitor_job()` iterates `self.monitored_interfaces` and `RNS.Transport.interfaces` while `teardown_interface()` can remove elements from both lists concurrently.

**Fix:** `list()` copies for both iterations.

---

#### [`9b87dc8`](../../commit/9b87dc8) **[MEDIUM]** Safe list iteration in Link.py to prevent RuntimeError.
`receive_packet()`, `link_closed()`, `handle_response()`, and `response_resource_concluded()` iterate `incoming_resources`, `outgoing_resources`, and `pending_requests`. These lists are modified by callbacks running in other threads (resource completion, request timeouts). Can cause RuntimeError or skipped elements.

**Fix:** `list()` copies for all 12 iteration sites. Same pattern as Transport.py fix (2daf166).

---

#### [`2daf166`](../../commit/2daf166) **[MEDIUM]** Fix unsafe list modification during iteration.
Three for-loops in `Transport.jobs()` remove elements from lists they are iterating — `pending_links`, `active_links`, `receipts`. Can skip elements or cause RuntimeError.

**Fix:** collect removals in a separate list, apply after iteration.

---

#### [`4e2650e`](../../commit/4e2650e) **[MEDIUM/REGRESSION]** Update detach() for new listener_filenos tuple format.
EPOLLHUP recovery fix changed `listener_filenos` from 2-tuples to 4-tuples. `detach()` still unpacked as 2-tuples — `ValueError` on shutdown.

**Fix:** indexed access consistent with all other sites.

---

#### [`8618496`](../../commit/8618496) **[HIGH]** Cap BackboneClientInterface transmit_buffer at 16MB.
`transmit_buffer` grows without limit when remote end is slow. Contributes to memory growth.

**Fix:** cap at 16MB, drop oldest data on overflow.

---

#### [`8facf00`](../../commit/8facf00) **[LOW]** Replace O(n) list.pop(0) with collections.deque for signal caches.
`local_client_rssi/snr/q_cache` use `list` with `pop(0)` — O(n) per eviction.

**Fix:** `collections.deque(maxlen=512)`.

---

#### [`48fd218`](../../commit/48fd218) **[HIGH]** Replace busy-wait spinlock with threading.Event.
`outbound()` and `inbound()` busy-wait on `jobs_running` with `sleep(0.0005)` — 2000 polls per second per waiting thread. Wastes CPU on weak hardware.

**Fix:** `threading.Event` — `wait()` blocks without CPU burn.

---

#### [`afa5868`](../../commit/afa5868) **[HIGH/SECURITY]** Replace exec() with importlib for external interface loading.
External interfaces loaded via `exec()` — arbitrary code execution from any `.py` file in the interfaces directory.

**Fix:** `importlib.util.spec_from_file_location()`.

---

#### [`804814b`](../../commit/804814b) **[HIGH]** Proper write lock for TCPClientInterface.process_outgoing().
Write lock was commented out. Concurrent calls from different threads can interleave HDLC framing on the socket, corrupting the byte stream.

**Fix:** `threading.Lock()` with `with` statement.

---

#### [`0ab3577`](../../commit/0ab3577) **[HIGH]** Add eviction for announce_rate_table and path_requests.
Both dicts grow without bound — entries added, never removed. On public transports seeing thousands of destinations, steady unbounded memory growth.

**Fix:** evict entries older than 1 hour during periodic table culling.

---

#### [`5cafaa8`](../../commit/5cafaa8) **[CRITICAL]** Release jobs_locked on early return in inbound().
Two `return` statements in `inbound()` exit after `jobs_locked = True` without releasing it. Triggered by MTU clamping exceptions on link request packets. Entire transport permanently deadlocked — no packets processed until restart. Silent failure.

**Fix:** add `Transport.jobs_locked = False` before both early returns.

---

#### [`c828e54`](../../commit/c828e54) **[MEDIUM]** Fix wrong config key for gateway mode.
In `_synthesize_interface()`, the gateway/gw branch inside the `interface_mode` block reads `c["mode"]` instead of `c["interface_mode"]`. Gateway mode silently never matches.

**Fix:** correct the key name.

---

#### [`e3074b7`](../../commit/e3074b7) **[CRITICAL]** Rate-limit "No interfaces could process" log spam.
When stale routes point to dead transports, every failed outbound packet logs at ERROR. During storms: thousands per second. The log I/O itself becomes a CPU amplifier — a single client was observed pinning a 2-vCPU Xeon at 100%.

**Fix:** max 1 log per 5 seconds with suppressed count.

---

#### [`9572428`](../../commit/9572428) **[HIGH]** O(1) membership check in clean_announce_cache.
`active_paths` and `tunnel_paths` built as lists. `not in` check is O(n) per file in the announce cache. Causes CPU spikes on transports with large path tables.

**Fix:** `list` → `set()`.

---

#### [`a26c360`](../../commit/a26c360) **[CRITICAL+HIGH]** Exponential backoff and frame_buffer cap for TCPClientInterface.
Same cascading failure and memory leak patterns as BackboneClientInterface, applied to the older TCPClientInterface.

**Fix:** backoff 5s → 300s cap, frame_buffer cap at 16MB.

---

#### [`e4995e4`](../../commit/e4995e4) **[HIGH]** Cap BackboneClientInterface frame_buffer at 16MB.
`frame_buffer` grows without limit on corrupted HDLC streams. Contributes to ~8MB/hour memory growth (230MB → 400MB+ in 21h). OOMs 512MB machines.

**Fix:** discard buffer at 16MB with warning.

---

#### [`ac6e518`](../../commit/ac6e518) **[CRITICAL]** Exponential backoff for BackboneClientInterface reconnect.
`reconnect()` retries every 5s forever. Dead bound transports burn 150%+ CPU in reconnect loops. Multiple dead binds cascade — one dead node takes down entire regions.

**Fix:** exponential backoff 5s → 10s → 20s → ... → 300s cap.

---

#### [`83f6cf3`](../../commit/83f6cf3) **[CRITICAL]** Recover BackboneInterface listener on EPOLLHUP.
When the listener socket receives EPOLLHUP, the code closes it and never recreates it. The `finally` block destroys ALL listeners. `_job_active` is never reset — event loop cannot restart. Process stays alive (systemd happy), transport deaf. Primary cause of ~30% uptime across community transport nodes.

**Fix:** recreate listener on EPOLLHUP, replace destructive `finally` with `_job_active = False`.

---

#### [`ba449db`](../../commit/ba449db) **[HIGH]** Eliminate double epoll.poll() call in BackboneInterface.
`__job()` calls `epoll.poll(1)` twice per loop iteration — stores result in `events`, then calls `poll()` again in the for-loop header. First result discarded, wastes 1s per iteration, can lose events.

**Fix:** iterate over stored `events` variable.

---

#### [`4312e20`](../../commit/4312e20) **[CRITICAL]** Increase BackboneInterface listen backlog from 1 to 512.
`server_socket.listen(1)` — connection queue of 1 on a public transport with 150+ clients. Kernel activates SYN cookies, drops connections.

**Fix:** `listen(512)`.



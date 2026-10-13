"""
Server-Sent Events (SSE) Manager.
Provides thread-safe broadcasting of real-time state changes to connected web clients
with heartbeat events and automatic cleanup upon client disconnection.
"""

import json
import queue
import threading
from typing import Generator, Dict, Any, Set

MAX_CLIENT_SUBSCRIBERS = 50
MAX_QUEUE_SIZE = 20
HEARTBEAT_TIMEOUT_SECONDS = 15.0


class EventManager:
    """
    Manages active SSE client connections and broadcasts messages.
    """

    def __init__(self, max_subscribers: int = MAX_CLIENT_SUBSCRIBERS):
        self._lock = threading.Lock()
        self._subscribers: Set[queue.Queue] = set()
        self._max_subscribers = max_subscribers

    def subscribe(self) -> queue.Queue:
        """
        Registers a new client queue. If subscriber limit is reached,
        cleans up or raises an exception.
        """
        q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        with self._lock:
            # If exceeding capacity, prune full queues or drop oldest
            if len(self._subscribers) >= self._max_subscribers:
                # Remove an arbitrary item to protect memory
                try:
                    oldest = next(iter(self._subscribers))
                    self._subscribers.remove(oldest)
                except StopIteration:
                    pass
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Removes a client queue upon connection close."""
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Pushes an event payload to all active client queues without blocking.
        """
        message = json.dumps({"event": event_type, "data": payload})
        with self._lock:
            active_subs = list(self._subscribers)

        for q in active_subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                # If client is slow, remove oldest item and insert newest
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    pass

    def stream(self, q: queue.Queue, initial_payload: Dict[str, Any] = None) -> Generator[str, None, None]:
        """
        Generator yielding SSE formatted strings to the Flask response stream.
        Periodically yields heartbeat comments to keep connections alive.
        """
        try:
            # Send initial message immediately if provided
            if initial_payload is not None:
                initial_msg = json.dumps({"event": "status", "data": initial_payload})
                yield f"data: {initial_msg}\n\n"

            while True:
                try:
                    msg = q.get(timeout=HEARTBEAT_TIMEOUT_SECONDS)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # Send periodic SSE comment as heartbeat
                    yield ": heartbeat\n\n"
        finally:
            self.unsubscribe(q)

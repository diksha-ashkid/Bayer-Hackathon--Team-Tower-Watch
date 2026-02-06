"""
Redis Memory Client for Incident Investigation

This module provides Redis integration for storing investigation state,
agent findings, and enabling the `sent_log_memory` functionality.

Features:
- Connection pooling with automatic reconnection
- JSON serialization for complex objects
- TTL-based expiration for investigation data
- Structured key schemas for different data types
- Thread-safe operations

Redis Key Schema:
- incident:{incident_id} - Full investigation state
- incident:{incident_id}:sent_log_memory - Log agent findings
- incident:{incident_id}:metrics_summary - Metrics agent findings
- incident:{incident_id}:deployment_events - Deploy agent findings
- incident:{incident_id}:investigation_steps - Step-by-step history

Usage:
    memory = RedisMemory()
    await memory.store_log_finding(incident_id, finding)
    findings = await memory.get_log_findings(incident_id)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Try to import redis, provide fallback for when not installed
try:
    import redis
    from redis import ConnectionPool
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis package not installed. Using in-memory fallback.")


@dataclass
class RedisConfig:
    """
    Configuration for Redis connection.
    
    Attributes:
        host: Redis server hostname.
        port: Redis server port.
        db: Redis database number.
        password: Optional Redis password.
        ssl: Whether to use SSL/TLS.
        socket_timeout: Socket timeout in seconds.
        retry_on_timeout: Whether to retry on timeout.
        default_ttl: Default TTL for investigation data (seconds).
    """
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    ssl: bool = False
    socket_timeout: int = 5
    retry_on_timeout: bool = True
    default_ttl: int = 86400  # 24 hours


class InMemoryFallback:
    """
    In-memory fallback when Redis is not available.
    
    Provides the same interface as Redis for development
    and testing without requiring a Redis server.
    """
    
    def __init__(self):
        self._store: dict[str, Any] = {}
        self._expiry: dict[str, datetime] = {}
        logger.info("Using in-memory storage (Redis fallback)")
    
    def _is_expired(self, key: str) -> bool:
        """Check if a key has expired."""
        if key in self._expiry:
            return datetime.utcnow() > self._expiry[key]
        return False
    
    def _clean_expired(self, key: str) -> None:
        """Remove expired key."""
        if self._is_expired(key):
            self._store.pop(key, None)
            self._expiry.pop(key, None)
    
    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set a key-value pair with optional expiry."""
        self._store[key] = value
        if ex:
            self._expiry[key] = datetime.utcnow() + timedelta(seconds=ex)
        return True
    
    def get(self, key: str) -> Optional[str]:
        """Get a value by key."""
        self._clean_expired(key)
        return self._store.get(key)
    
    def delete(self, key: str) -> int:
        """Delete a key."""
        self._clean_expired(key)
        if key in self._store:
            del self._store[key]
            self._expiry.pop(key, None)
            return 1
        return 0
    
    def exists(self, key: str) -> int:
        """Check if key exists."""
        self._clean_expired(key)
        return 1 if key in self._store else 0
    
    def lpush(self, key: str, *values: str) -> int:
        """Push values to list (left)."""
        self._clean_expired(key)
        if key not in self._store:
            self._store[key] = []
        for value in values:
            self._store[key].insert(0, value)
        return len(self._store[key])
    
    def rpush(self, key: str, *values: str) -> int:
        """Push values to list (right)."""
        self._clean_expired(key)
        if key not in self._store:
            self._store[key] = []
        for value in values:
            self._store[key].append(value)
        return len(self._store[key])
    
    def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Get range of list values."""
        self._clean_expired(key)
        if key not in self._store:
            return []
        lst = self._store[key]
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]
    
    def hset(self, key: str, mapping: dict[str, str]) -> int:
        """Set hash fields."""
        self._clean_expired(key)
        if key not in self._store:
            self._store[key] = {}
        self._store[key].update(mapping)
        return len(mapping)
    
    def hget(self, key: str, field: str) -> Optional[str]:
        """Get hash field value."""
        self._clean_expired(key)
        if key in self._store and isinstance(self._store[key], dict):
            return self._store[key].get(field)
        return None
    
    def hgetall(self, key: str) -> dict[str, str]:
        """Get all hash fields."""
        self._clean_expired(key)
        if key in self._store and isinstance(self._store[key], dict):
            return self._store[key].copy()
        return {}
    
    def expire(self, key: str, seconds: int) -> bool:
        """Set key expiry."""
        if key in self._store:
            self._expiry[key] = datetime.utcnow() + timedelta(seconds=seconds)
            return True
        return False
    
    def ping(self) -> bool:
        """Health check."""
        return True
    
    def close(self) -> None:
        """Close connection (no-op for in-memory)."""
        pass


class RedisMemory:
    """
    Redis-based memory store for incident investigation.
    
    Provides persistent storage for investigation state, agent findings,
    and enables the sent_log_memory feature for log analysis.
    
    The memory store uses structured keys to organize data:
    - Investigation state is stored as JSON blobs
    - Findings are stored in Redis lists for efficient appending
    - Summaries are stored in Redis hashes for field-level access
    
    Example:
        memory = RedisMemory()
        
        # Store a log finding
        await memory.store_log_finding(
            incident_id="inc-123",
            finding={"error_type": "TypeError", ...}
        )
        
        # Get all findings for an incident
        findings = await memory.get_sent_log_memory(incident_id)
    """
    
    def __init__(self, config: Optional[RedisConfig] = None):
        """
        Initialize the Redis memory client.
        
        Args:
            config: Optional configuration. If not provided, loads from
                   environment variables.
        """
        self.config = config or self._load_config_from_env()
        self._client: Optional[Any] = None
        self._pool: Optional[Any] = None
        
        logger.info(
            f"RedisMemory initialized for {self.config.host}:{self.config.port}"
        )
    
    def _load_config_from_env(self) -> RedisConfig:
        """
        Load Redis configuration from environment variables.
        
        Environment Variables:
            REDIS_HOST: Redis server hostname
            REDIS_PORT: Redis server port
            REDIS_DB: Redis database number
            REDIS_PASSWORD: Redis password
            REDIS_SSL: Whether to use SSL
            REDIS_TTL: Default TTL in seconds
        
        Returns:
            RedisConfig populated from environment.
        """
        return RedisConfig(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=int(os.environ.get("REDIS_DB", "0")),
            password=os.environ.get("REDIS_PASSWORD"),
            ssl=os.environ.get("REDIS_SSL", "false").lower() == "true",
            socket_timeout=int(os.environ.get("REDIS_TIMEOUT", "5")),
            default_ttl=int(os.environ.get("REDIS_TTL", "86400")),
        )
    
    @property
    def client(self):
        """
        Get or create the Redis client.
        
        Uses connection pooling for efficient connection management.
        Falls back to in-memory storage if Redis is not available.
        
        Returns:
            Redis client or InMemoryFallback.
        """
        if self._client is None:
            if not REDIS_AVAILABLE:
                self._client = InMemoryFallback()
            else:
                try:
                    self._pool = ConnectionPool(
                        host=self.config.host,
                        port=self.config.port,
                        db=self.config.db,
                        password=self.config.password,
                        socket_timeout=self.config.socket_timeout,
                        retry_on_timeout=self.config.retry_on_timeout,
                        decode_responses=True,
                    )
                    self._client = redis.Redis(connection_pool=self._pool)
                    # Test connection
                    self._client.ping()
                    logger.info("Redis connection established successfully")
                except Exception as e:
                    logger.warning(f"Redis connection failed: {e}. Using fallback.")
                    self._client = InMemoryFallback()
        
        return self._client
    
    # Key generation helpers
    def _incident_key(self, incident_id: str) -> str:
        """Generate key for incident state."""
        return f"incident:{incident_id}"
    
    def _log_memory_key(self, incident_id: str) -> str:
        """Generate key for sent_log_memory."""
        return f"incident:{incident_id}:sent_log_memory"
    
    def _metrics_key(self, incident_id: str) -> str:
        """Generate key for metrics summary."""
        return f"incident:{incident_id}:metrics_summary"
    
    def _deploy_key(self, incident_id: str) -> str:
        """Generate key for deployment events."""
        return f"incident:{incident_id}:deployment_events"
    
    def _steps_key(self, incident_id: str) -> str:
        """Generate key for investigation steps."""
        return f"incident:{incident_id}:investigation_steps"
    
    # Investigation State Methods
    
    def store_investigation_state(
        self,
        incident_id: str,
        state: dict[str, Any],
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Store the full investigation state.
        
        Args:
            incident_id: Unique incident identifier.
            state: Investigation state dictionary.
            ttl: Optional TTL override in seconds.
            
        Returns:
            True if successful, False otherwise.
        """
        key = self._incident_key(incident_id)
        try:
            self.client.set(
                key,
                json.dumps(state, default=str),
                ex=ttl or self.config.default_ttl,
            )
            logger.debug(f"Stored investigation state for {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store investigation state: {e}")
            return False
    
    def get_investigation_state(
        self,
        incident_id: str,
    ) -> Optional[dict[str, Any]]:
        """
        Retrieve the full investigation state.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            Investigation state dictionary or None if not found.
        """
        key = self._incident_key(incident_id)
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get investigation state: {e}")
            return None
    
    # Sent Log Memory Methods (Log Agent)
    
    def store_log_finding(
        self,
        incident_id: str,
        finding: dict[str, Any],
    ) -> bool:
        """
        Store a log finding in sent_log_memory.
        
        This implements the Sentry-like deduplication memory,
        tracking which log patterns have already been processed.
        
        Args:
            incident_id: Unique incident identifier.
            finding: Log finding dictionary.
            
        Returns:
            True if successful, False otherwise.
        """
        key = self._log_memory_key(incident_id)
        try:
            self.client.rpush(key, json.dumps(finding, default=str))
            self.client.expire(key, self.config.default_ttl)
            logger.debug(f"Stored log finding for {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store log finding: {e}")
            return False
    
    def get_sent_log_memory(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all log findings from sent_log_memory.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            List of log finding dictionaries.
        """
        key = self._log_memory_key(incident_id)
        try:
            data = self.client.lrange(key, 0, -1)
            return [json.loads(item) for item in data]
        except Exception as e:
            logger.error(f"Failed to get sent_log_memory: {e}")
            return []
    
    # Metrics Summary Methods (Metrics Agent)
    
    def store_metrics_summary(
        self,
        incident_id: str,
        summary: dict[str, Any],
    ) -> bool:
        """
        Store metrics summary from the Metrics Agent.
        
        Args:
            incident_id: Unique incident identifier.
            summary: Metrics summary dictionary.
            
        Returns:
            True if successful, False otherwise.
        """
        key = self._metrics_key(incident_id)
        try:
            # Flatten nested dicts for Redis hash storage
            flat_summary = {k: json.dumps(v, default=str) for k, v in summary.items()}
            self.client.hset(key, mapping=flat_summary)
            self.client.expire(key, self.config.default_ttl)
            logger.debug(f"Stored metrics summary for {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store metrics summary: {e}")
            return False
    
    def get_metrics_summary(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        """
        Retrieve metrics summary.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            Metrics summary dictionary.
        """
        key = self._metrics_key(incident_id)
        try:
            data = self.client.hgetall(key)
            return {k: json.loads(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to get metrics summary: {e}")
            return {}
    
    # Deployment Events Methods (Deploy Agent)
    
    def store_deployment_event(
        self,
        incident_id: str,
        event: dict[str, Any],
    ) -> bool:
        """
        Store a deployment event.
        
        Args:
            incident_id: Unique incident identifier.
            event: Deployment event dictionary.
            
        Returns:
            True if successful, False otherwise.
        """
        key = self._deploy_key(incident_id)
        try:
            self.client.rpush(key, json.dumps(event, default=str))
            self.client.expire(key, self.config.default_ttl)
            logger.debug(f"Stored deployment event for {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store deployment event: {e}")
            return False
    
    def get_deployment_events(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all deployment events.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            List of deployment event dictionaries.
        """
        key = self._deploy_key(incident_id)
        try:
            data = self.client.lrange(key, 0, -1)
            return [json.loads(item) for item in data]
        except Exception as e:
            logger.error(f"Failed to get deployment events: {e}")
            return []
    
    # Investigation Steps Methods
    
    def store_investigation_step(
        self,
        incident_id: str,
        step: dict[str, Any],
    ) -> bool:
        """
        Store an investigation step.
        
        Args:
            incident_id: Unique incident identifier.
            step: Investigation step dictionary.
            
        Returns:
            True if successful, False otherwise.
        """
        key = self._steps_key(incident_id)
        try:
            self.client.rpush(key, json.dumps(step, default=str))
            self.client.expire(key, self.config.default_ttl)
            logger.debug(f"Stored investigation step for {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store investigation step: {e}")
            return False
    
    def get_investigation_steps(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all investigation steps.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            List of investigation step dictionaries.
        """
        key = self._steps_key(incident_id)
        try:
            data = self.client.lrange(key, 0, -1)
            return [json.loads(item) for item in data]
        except Exception as e:
            logger.error(f"Failed to get investigation steps: {e}")
            return []
    
    def clear_incident_data(self, incident_id: str) -> bool:
        """
        Clear all data for an incident.
        
        Args:
            incident_id: Unique incident identifier.
            
        Returns:
            True if successful, False otherwise.
        """
        keys = [
            self._incident_key(incident_id),
            self._log_memory_key(incident_id),
            self._metrics_key(incident_id),
            self._deploy_key(incident_id),
            self._steps_key(incident_id),
        ]
        try:
            for key in keys:
                self.client.delete(key)
            logger.info(f"Cleared all data for incident {incident_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear incident data: {e}")
            return False
    
    def health_check(self) -> bool:
        """
        Verify Redis connection is healthy.
        
        Returns:
            True if Redis is reachable, False otherwise.
        """
        try:
            return self.client.ping()
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False
    
    def close(self) -> None:
        """Close the Redis connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if self._pool:
            try:
                self._pool.disconnect()
            except Exception:
                pass
            self._pool = None

"""
Prompt Cache Service

Caches generated prompts in PostgreSQL for efficiency.
Uses org_id, user_id, and content_hash for unique identification.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
import os
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DB_URL")


class PromptCacheService:
    """
    Service for caching generated prompts in PostgreSQL.
    
    Schema:
    - org_id: Organization identifier
    - user_id: User identifier  
    - content_hash: SHA-256 hash of content
    - prompt: Generated prompt text
    - intent: Call intent type
    - metadata: Additional metadata (JSON)
    - created_at: Timestamp
    - updated_at: Timestamp
    """
    
    def __init__(self, db_url: str = None):
        self.db_url = db_url or DB_URL
        self._pool = None
    
    async def _get_pool(self):
        """Get or create connection pool."""
        if self._pool is None:
            try:
                import asyncpg
                self._pool = await asyncpg.create_pool(self.db_url)
                await self._ensure_table()
            except Exception as e:
                print(f"Warning: Could not connect to database: {e}")
                return None
        return self._pool
    
    async def _ensure_table(self):
        """Create the prompt_cache table if it doesn't exist."""
        pool = await self._get_pool()
        if not pool:
            return
            
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_cache (
                    id SERIAL PRIMARY KEY,
                    org_id VARCHAR(255) NOT NULL,
                    user_id VARCHAR(255) NOT NULL,
                    content_hash VARCHAR(64) NOT NULL,
                    prompt TEXT NOT NULL,
                    intent VARCHAR(50) DEFAULT 'general',
                    metadata JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(org_id, user_id, content_hash)
                );
                
                CREATE INDEX IF NOT EXISTS idx_prompt_cache_lookup 
                ON prompt_cache(org_id, user_id, content_hash);
            """)
    
    async def get_cached_prompt(
        self,
        org_id: str,
        user_id: str,
        content_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a cached prompt by org_id, user_id, and content_hash.
        
        Args:
            org_id: Organization identifier
            user_id: User identifier
            content_hash: SHA-256 hash of content
            
        Returns:
            Cached prompt data or None if not found
        """
        pool = await self._get_pool()
        if not pool:
            return None
            
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT prompt, intent, metadata, created_at, updated_at
                FROM prompt_cache
                WHERE org_id = $1 AND user_id = $2 AND content_hash = $3
            """, org_id, user_id, content_hash)
            
            if row:
                return {
                    "prompt": row["prompt"],
                    "intent": row["intent"],
                    "metadata": row["metadata"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                }
        return None
    
    async def save_prompt(
        self,
        org_id: str,
        user_id: str,
        content_hash: str,
        prompt: str,
        intent: str = "general",
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Save a prompt to the cache.
        
        Args:
            org_id: Organization identifier
            user_id: User identifier
            content_hash: SHA-256 hash of content
            prompt: Generated prompt text
            intent: Call intent type
            metadata: Additional metadata
            
        Returns:
            True if saved successfully, False otherwise
        """
        pool = await self._get_pool()
        if not pool:
            return False
            
        import json
        
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO prompt_cache (org_id, user_id, content_hash, prompt, intent, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (org_id, user_id, content_hash)
                DO UPDATE SET 
                    prompt = EXCLUDED.prompt,
                    intent = EXCLUDED.intent,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
            """, org_id, user_id, content_hash, prompt, intent, 
            json.dumps(metadata) if metadata else None)
        
        return True
    
    async def delete_cached_prompt(
        self,
        org_id: str,
        user_id: str,
        content_hash: str = None
    ) -> int:
        """
        Delete cached prompts.
        
        Args:
            org_id: Organization identifier
            user_id: User identifier
            content_hash: Optional specific hash to delete (deletes all for org/user if not provided)
            
        Returns:
            Number of rows deleted
        """
        pool = await self._get_pool()
        if not pool:
            return 0
            
        async with pool.acquire() as conn:
            if content_hash:
                result = await conn.execute("""
                    DELETE FROM prompt_cache
                    WHERE org_id = $1 AND user_id = $2 AND content_hash = $3
                """, org_id, user_id, content_hash)
            else:
                result = await conn.execute("""
                    DELETE FROM prompt_cache
                    WHERE org_id = $1 AND user_id = $2
                """, org_id, user_id)
            
            # Extract deleted count from result string
            try:
                return int(result.split()[-1])
            except:
                return 0
    
    async def list_cached_prompts(
        self,
        org_id: str,
        user_id: str,
        limit: int = 10
    ) -> list:
        """
        List cached prompts for an org/user.
        
        Args:
            org_id: Organization identifier
            user_id: User identifier
            limit: Maximum number of results
            
        Returns:
            List of cached prompt metadata
        """
        pool = await self._get_pool()
        if not pool:
            return []
            
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT content_hash, intent, metadata, created_at, updated_at
                FROM prompt_cache
                WHERE org_id = $1 AND user_id = $2
                ORDER BY updated_at DESC
                LIMIT $3
            """, org_id, user_id, limit)
            
            return [
                {
                    "content_hash": row["content_hash"],
                    "intent": row["intent"],
                    "metadata": row["metadata"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                }
                for row in rows
            ]

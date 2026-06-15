import asyncpg
from db.migrations import run_database_migrations

class DatabaseConnection:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None
        self.users = None
        self.endpoints = None
        self.tasks = None

    async def connect(self) -> None:
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                self.db_url, 
                min_size=1, 
                max_size=10
            )
            async with self.pool.acquire() as conn:
                await run_database_migrations(conn)
            
            # Lazy load repositories to break absolute circular dependency patterns
            from db.repositories.users import UserRepository
            from db.repositories.endpoints import EndpointRepository
            from db.repositories.tasks import TaskRepository
            
            self.users = UserRepository(self.pool)
            self.endpoints = EndpointRepository(self.pool)
            self.tasks = TaskRepository(self.pool)

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None
from app.db.database import engine, Base
from app.models import user, order
import asyncio

async def init_db():
    """Initialize database by creating all tables"""
    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
    
    print("Database tables created successfully!")

if __name__ == "__main__":
    asyncio.run(init_db()) 
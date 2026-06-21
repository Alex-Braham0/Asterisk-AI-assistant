import asyncpg

async def run_database_migrations(conn: asyncpg.Connection) -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS Users (
        id SERIAL PRIMARY KEY,
        primary_name VARCHAR(255) NOT NULL,
        aliases TEXT[] DEFAULT '{}',
        current_timezone VARCHAR(100) DEFAULT 'Europe/London',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS Endpoints (
        extension VARCHAR(50) PRIMARY KEY,
        display_name VARCHAR(255),
        device_type VARCHAR(50) DEFAULT 'STATIC_SHARED', 
        physical_location VARCHAR(255),
        is_active BOOLEAN DEFAULT TRUE,
        default_timezone VARCHAR(100) DEFAULT 'Europe/London'
    );

    CREATE TABLE IF NOT EXISTS Endpoint_Users (
        extension VARCHAR(50) REFERENCES Endpoints(extension) ON DELETE CASCADE,
        user_id INT REFERENCES Users(id) ON DELETE CASCADE,
        is_default BOOLEAN DEFAULT FALSE,
        access_level VARCHAR(50) DEFAULT 'SHARED_ONLY', 
        PRIMARY KEY (extension, user_id)
    );

    CREATE TABLE IF NOT EXISTS Tasks (
        id SERIAL PRIMARY KEY,
        task_type VARCHAR(100) NOT NULL,
        payload TEXT NOT NULL,
        scheduled_time TIMESTAMP NOT NULL,
        status VARCHAR(50) DEFAULT 'pending'
    );

    CREATE TABLE IF NOT EXISTS Autonomous_Missions (
        id SERIAL PRIMARY KEY,
        owner_user_id INT REFERENCES Users(id) ON DELETE SET NULL,
        run_at_utc TIMESTAMP NOT NULL,
        mission_directive TEXT NOT NULL,
        status VARCHAR(50) DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    await conn.execute(schema)
    
    # Critique 2: Dynamic alter column sequence patching pre-existing table schemas safely
    try:
        await conn.execute("ALTER TABLE Users RENAME COLUMN name TO primary_name;")
    except asyncpg.exceptions.UndefinedColumnError:
        pass
    except asyncpg.exceptions.DuplicateColumnError:
        pass 
        
    migration_patches = """
    ALTER TABLE Users ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}';
    ALTER TABLE Endpoints ADD COLUMN IF NOT EXISTS device_type VARCHAR(50) DEFAULT 'STATIC_SHARED';
    ALTER TABLE Endpoints ADD COLUMN IF NOT EXISTS physical_location VARCHAR(255);
    ALTER TABLE Endpoint_Users ADD COLUMN IF NOT EXISTS access_level VARCHAR(50) DEFAULT 'SHARED_ONLY';
<<<<<<< HEAD
    ALTER TABLE Users ADD COLUMN IF NOT EXISTS public_memory TEXT DEFAULT 'No existing memory profile.';
    ALTER TABLE Users ADD COLUMN IF NOT EXISTS private_memory TEXT DEFAULT 'No existing memory profile.';
    ALTER TABLE Endpoints ADD COLUMN IF NOT EXISTS endpoint_memory TEXT DEFAULT 'No specific memory data.';
=======
    ALTER TABLE Autonomous_Missions ADD COLUMN IF NOT EXISTS final_report TEXT; -- NEW
>>>>>>> feature/local-dashboard
    """
    await conn.execute(migration_patches)
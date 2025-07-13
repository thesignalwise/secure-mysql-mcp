import json
import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from pathlib import Path
import sys

# MCP imports
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# MySQL imports
import aiomysql
from cryptography.fernet import Fernet

# Setup logging with more detail
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
)
logger = logging.getLogger(__name__)

def print_usage():
    """Print usage information"""
    print("""
Secure MySQL MCP

Usage:
    python secure_mysql_mcp_server.py [config_file]

Arguments:
    config_file    Path to the JSON configuration file (default: config/servers.json)

Examples:
    python secure_mysql_mcp_server.py                    # Uses default config/servers.json
    python secure_mysql_mcp_server.py production.json    # Uses config/production.json
    python secure_mysql_mcp_server.py /path/to/config.json  # Uses absolute path
    python secure_mysql_mcp_server.py config/dev.json    # Uses specific path

If the configuration file doesn't exist, a sample will be created.
    """)

class PasswordManager:
    """Simple password encryption/decryption manager"""
    def __init__(self, key: Optional[bytes] = None):
        logger.debug("Initializing PasswordManager")
        if key:
            # If key is provided as bytes and is the right length for Fernet
            if isinstance(key, bytes) and len(key) == 44:
                self.cipher = Fernet(key)
            else:
                # Try to create Fernet with the provided key
                try:
                    self.cipher = Fernet(key)
                except:
                    # If it fails, generate a new key
                    logger.warning("Invalid encryption key provided, generating new one")
                    self.cipher = Fernet(Fernet.generate_key())
        else:
            self.cipher = Fernet(Fernet.generate_key())
        logger.debug("PasswordManager initialized successfully")
    
    def encrypt(self, password: str) -> str:
        return self.cipher.encrypt(password.encode()).decode()
    
    def decrypt(self, encrypted_password: str) -> str:
        return self.cipher.decrypt(encrypted_password.encode()).decode()

class ConnectionManager:
    """Manages MySQL connections and connection pools"""
    def __init__(self):
        logger.debug("Initializing ConnectionManager")
        self.servers: Dict[str, dict] = {}
        self.pools: Dict[str, aiomysql.Pool] = {}
        self.active_connections: Dict[str, Tuple[str, str]] = {}  # session_id -> (server_id, database)
        self.password_manager = PasswordManager()
        logger.debug("ConnectionManager initialized")
    
    async def load_config(self, config_path: str):
        """Load server configurations from JSON file"""
        logger.info(f"Loading configuration from {config_path}")
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            # Check if we have an encryption key
            if 'encryption_key' in config:
                logger.debug("Found encryption key in config")
                self.password_manager = PasswordManager(config['encryption_key'].encode('utf-8'))
            
            # Only load enabled servers
            self.servers = {}
            enabled_count = 0
            disabled_count = 0
            
            for server in config['servers']:
                if server.get('enabled', True):  # Default to True if not specified
                    self.servers[server['id']] = server
                    enabled_count += 1
                    logger.debug(f"Loaded server: {server['id']} ({server.get('alias', 'N/A')})")
                else:
                    disabled_count += 1
                    logger.info(f"Skipping disabled server: {server['id']} ({server.get('alias', 'N/A')})")
            
            logger.info(f"Loaded {enabled_count} enabled server(s), skipped {disabled_count} disabled server(s)")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise
    
    async def create_pool(self, server_id: str) -> aiomysql.Pool:
        """Create a connection pool for a server"""
        logger.debug(f"Creating connection pool for {server_id}")
        if server_id not in self.servers:
            raise ValueError(f"Server {server_id} not found in configuration")
        
        if server_id in self.pools:
            logger.debug(f"Pool already exists for {server_id}")
            return self.pools[server_id]
        
        server = self.servers[server_id]
        
        # Decrypt password if needed
        password = server['password']
        if server.get('encrypted', True):
            try:
                password = self.password_manager.decrypt(password)
                logger.debug(f"Password decrypted for {server_id}")
            except:
                logger.warning(f"Failed to decrypt password for {server_id}, using as plain text")
        
        # Create pool
        pool_params = {
            'host': server['host'],
            'port': server.get('port', 3306),
            'user': server['user'],
            'password': password,
            'minsize': 1,
            'maxsize': server.get('max_connections', 5),
            'connect_timeout': server.get('connection_timeout', 10),
            'echo': False
        }
        
        # Add default database if specified
        if 'default_database' in server:
            pool_params['db'] = server['default_database']
            logger.debug(f"Using default database: {server['default_database']}")
        
        pool = await aiomysql.create_pool(**pool_params)
        
        self.pools[server_id] = pool
        logger.info(f"Created connection pool for {server_id}")
        return pool
    
    async def get_connection(self, server_id: str, database: Optional[str] = None) -> aiomysql.Connection:
        """Get a connection from the pool"""
        logger.debug(f"Getting connection for {server_id}, database: {database}")
        pool = await self.create_pool(server_id)
        conn = await pool.acquire()
        
        if database:
            await conn.select_db(database)
            logger.debug(f"Selected database: {database}")
        
        return conn
    
    async def release_connection(self, server_id: str, conn: aiomysql.Connection):
        """Release connection back to pool"""
        logger.debug(f"Releasing connection for {server_id}")
        if server_id in self.pools:
            self.pools[server_id].release(conn)
    
    async def close_all(self):
        """Close all connection pools"""
        logger.info("Closing all connection pools")
        for server_id, pool in self.pools.items():
            pool.close()
            await pool.wait_closed()
            logger.debug(f"Closed pool for {server_id}")
        self.pools.clear()
        self.active_connections.clear()

class MySQLMCPServer:
    """Secure MySQL MCP implementation"""
    def __init__(self, config_path: str = "config/servers.json"):
        logger.info("Initializing Secure MySQL MCP")
        self.server = Server("secure-mysql-mcp")
        self.connection_manager = ConnectionManager()
        self.config_path = config_path
        self.current_session: Optional[str] = None
        
        # Setup handlers
        logger.info("Setting up handlers")
        self._setup_handlers()
        logger.info("Secure MySQL MCP initialization complete")
    
    def _setup_handlers(self):
        """Setup tool handlers"""
        logger.info("_setup_handlers called")
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            logger.info("=== handle_list_tools called by MCP client ===")
            tools = [
                Tool(
                    name="list_available_databases",
                    description="List all configured MySQL servers and their available databases",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="connect_database",
                    description="Connect to a specific database on a MySQL server",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "server_id": {
                                "type": "string",
                                "description": "ID of the MySQL server"
                            },
                            "database": {
                                "type": "string",
                                "description": "Name of the database to connect to"
                            }
                        },
                        "required": ["server_id", "database"]
                    }
                ),
                Tool(
                    name="disconnect_database",
                    description="Disconnect from the current database",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "server_id": {
                                "type": "string",
                                "description": "ID of the MySQL server to disconnect from (optional, disconnects current if not specified)"
                            }
                        },
                        "required": []
                    }
                ),
                Tool(
                    name="execute_sql",
                    description="Execute a SQL query on the connected database",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "SQL query to execute"
                            },
                            "server_id": {
                                "type": "string",
                                "description": "Server ID (optional, uses current connection if not specified)"
                            },
                            "database": {
                                "type": "string",
                                "description": "Database name (optional, uses current connection if not specified)"
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="get_connection_status",
                    description="Get current connection status",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                )
            ]
            logger.info(f"Returning {len(tools)} tools to MCP client")
            for tool in tools:
                logger.debug(f"  Tool: {tool.name}")
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            logger.info(f"=== handle_call_tool called: {name} ===")
            
            # Log arguments safely - mask sensitive data
            safe_arguments = arguments.copy()
            if 'query' in safe_arguments and len(safe_arguments['query']) > 30:
                safe_arguments['query'] = safe_arguments['query'][:30] + "..."
            logger.debug(f"Arguments: {json.dumps(safe_arguments, indent=2)}")
            
            try:
                if name == "list_available_databases":
                    result = await self._list_available_databases()
                elif name == "connect_database":
                    result = await self._connect_database(
                        arguments["server_id"],
                        arguments["database"]
                    )
                elif name == "disconnect_database":
                    result = await self._disconnect_database(
                        arguments.get("server_id")
                    )
                elif name == "execute_sql":
                    result = await self._execute_sql(
                        arguments["query"],
                        arguments.get("server_id"),
                        arguments.get("database")
                    )
                elif name == "get_connection_status":
                    result = await self._get_connection_status()
                else:
                    logger.error(f"Unknown tool: {name}")
                    result = {"error": f"Unknown tool: {name}"}
                
                # Log result safely - don't include sensitive data
                safe_result = self._sanitize_result_for_logging(result)
                logger.debug(f"Tool {name} result: {json.dumps(safe_result, indent=2, default=str)}")
                
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str)
                )]
            except Exception as e:
                logger.error(f"Error in tool {name}: {e}", exc_info=True)
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": str(e)}, indent=2)
                )]
        
        logger.info("Handlers setup complete")
    
    def _sanitize_result_for_logging(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize result for logging by removing sensitive information"""
        if not isinstance(result, dict):
            return result
        
        sanitized = result.copy()
        
        # Remove sensitive fields from servers list
        if 'servers' in sanitized:
            sanitized_servers = []
            for server in sanitized['servers']:
                safe_server = server.copy()
                # Remove sensitive connection details
                safe_server.pop('password', None)
                safe_server.pop('user', None)
                safe_server.pop('host', None)
                safe_server.pop('port', None)
                sanitized_servers.append(safe_server)
            sanitized['servers'] = sanitized_servers
        
        # Truncate long query results
        if 'rows' in sanitized and len(sanitized['rows']) > 5:
            sanitized['rows'] = sanitized['rows'][:5] + [{"...": f"and {len(sanitized['rows']) - 5} more rows"}]
        
        return sanitized
    
    async def _list_available_databases(self) -> Dict[str, Any]:
        """List all available MySQL servers and their databases"""
        logger.debug("_list_available_databases called")
        result = {"servers": [], "summary": {"total": 0, "enabled": 0, "connected": 0, "errors": 0}}
        
        for server_id, server_config in self.connection_manager.servers.items():
            # Skip if explicitly disabled (though it shouldn't be in servers dict)
            if not server_config.get('enabled', True):
                continue
                
            result["summary"]["total"] += 1
            result["summary"]["enabled"] += 1
            
            server_info = {
                "id": server_id,
                "alias": server_config.get("alias", server_id),
                "status": "available",
                "default_database": server_config.get("default_database"),
                "databases": [],
                "permissions": server_config.get("permissions", [])
            }
            
            try:
                logger.debug(f"Connecting to {server_id} to list databases")
                # Try to connect and list databases
                conn = await self.connection_manager.get_connection(server_id)
                async with conn.cursor() as cursor:
                    await cursor.execute("SHOW DATABASES")
                    databases = await cursor.fetchall()
                    server_info["databases"] = [db[0] for db in databases]
                    server_info["status"] = "connected"
                    result["summary"]["connected"] += 1
                    logger.debug(f"Found {len(server_info['databases'])} databases on {server_id}")
                await self.connection_manager.release_connection(server_id, conn)
            except Exception as e:
                server_info["status"] = "error"
                server_info["error"] = str(e)
                result["summary"]["errors"] += 1
                logger.error(f"Failed to list databases for {server_id}: {e}")
            
            result["servers"].append(server_info)
        
        logger.debug(f"Returning info for {len(result['servers'])} servers")
        return result
    
    async def _connect_database(self, server_id: str, database: str) -> Dict[str, Any]:
        """Connect to a specific database"""
        logger.debug(f"_connect_database called: {server_id}/{database}")
        try:
            # Test connection
            conn = await self.connection_manager.get_connection(server_id, database)
            
            # Store active connection info
            self.connection_manager.active_connections[self.current_session] = (server_id, database)
            logger.debug(f"Stored active connection for session {self.current_session}")
            
            # Get some basic info
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT DATABASE()")
                current_db = await cursor.fetchone()
                
                await cursor.execute("SELECT VERSION()")
                version = await cursor.fetchone()
            
            await self.connection_manager.release_connection(server_id, conn)
            
            result = {
                "status": "connected",
                "server_id": server_id,
                "database": database,
                "current_database": current_db[0] if current_db else None,
                "mysql_version": version[0] if version else None,
                "session_id": self.current_session
            }
            logger.info(f"Successfully connected to {server_id}/{database}")
            return result
        except Exception as e:
            logger.error(f"Failed to connect to {server_id}/{database}: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _disconnect_database(self, server_id: Optional[str] = None) -> Dict[str, Any]:
        """Disconnect from database"""
        logger.debug(f"_disconnect_database called: server_id={server_id}")
        if server_id:
            # Disconnect specific server
            if server_id in self.connection_manager.pools:
                pool = self.connection_manager.pools[server_id]
                pool.close()
                await pool.wait_closed()
                del self.connection_manager.pools[server_id]
                logger.info(f"Disconnected from {server_id}")
            
            # Remove from active connections
            to_remove = []
            for session, (sid, _) in self.connection_manager.active_connections.items():
                if sid == server_id:
                    to_remove.append(session)
            for session in to_remove:
                del self.connection_manager.active_connections[session]
            
            return {"status": "disconnected", "server_id": server_id}
        else:
            # Disconnect current session
            if self.current_session in self.connection_manager.active_connections:
                del self.connection_manager.active_connections[self.current_session]
                logger.info(f"Disconnected session {self.current_session}")
                return {"status": "disconnected", "session_id": self.current_session}
            else:
                logger.debug("No active connection to disconnect")
                return {"status": "not_connected"}
    
    async def _execute_sql(self, query: str, server_id: Optional[str] = None, 
                          database: Optional[str] = None) -> Dict[str, Any]:
        """Execute SQL query"""
        # Log safely - don't include full query content for security
        query_preview = query[:30] + "..." if len(query) > 30 else query
        logger.debug(f"_execute_sql called: query='{query_preview}', server_id={server_id}, database={database}")
        
        # Determine which connection to use
        if not server_id and self.current_session in self.connection_manager.active_connections:
            server_id, database = self.connection_manager.active_connections[self.current_session]
            logger.debug(f"Using active connection: {server_id}/{database}")
        elif not server_id:
            # Try to use the first available server with default database
            for sid, server in self.connection_manager.servers.items():
                if 'default_database' in server:
                    server_id = sid
                    database = server['default_database']
                    logger.debug(f"Using default database: {server_id}/{database}")
                    break
            
            if not server_id:
                logger.error("No active connection and no default database")
                return {"error": "No active connection and no default database configured. Please connect to a database first."}
        
        # If no database specified, check if server has default
        if not database and server_id in self.connection_manager.servers:
            database = self.connection_manager.servers[server_id].get('default_database')
            
        if not database:
            logger.error(f"No database specified for server {server_id}")
            return {"error": f"No database specified for server {server_id}. Please connect to a database first."}
        
        # SQL permission check - only restrict if server has READ_ONLY permission
        server_permissions = self.connection_manager.servers.get(server_id, {}).get('permissions', [])
        if 'READ_ONLY' in server_permissions:
            # Only block dangerous operations for READ_ONLY servers
            dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'CREATE', 'ALTER', 'TRUNCATE', 'GRANT', 'REVOKE']
            query_upper = query.upper()
            for keyword in dangerous_keywords:
                if keyword in query_upper:
                    logger.warning(f"Query contains restricted keyword '{keyword}' for READ_ONLY server {server_id}")
                    return {"error": f"Query contains restricted keyword '{keyword}' - server {server_id} is configured as READ-only"}
        
        try:
            start_time = datetime.now()
            conn = await self.connection_manager.get_connection(server_id, database)
            
            async with conn.cursor() as cursor:
                logger.debug(f"Executing query on {server_id}/{database}")
                await cursor.execute(query)
                
                # Improved query type detection
                query_upper = query.strip().upper()
                # Check if it's a query that returns results
                is_result_query = any(query_upper.startswith(cmd) for cmd in [
                    'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN', 'WITH'
                ])
                
                if is_result_query and cursor.description:
                    # This is a query that returns results
                    logger.debug("Query returns results")
                    rows_raw = await cursor.fetchall()
                    columns = [desc[0] for desc in cursor.description]
                    
                    # Convert tuples to dictionaries
                    rows = []
                    for row in rows_raw:
                        row_dict = {}
                        for i, value in enumerate(row):
                            row_dict[columns[i]] = value
                        rows.append(row_dict)
                    
                    result = {
                        "status": "success",
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "execution_time": (datetime.now() - start_time).total_seconds()
                    }
                    logger.info(f"Query returned {len(rows)} rows")
                else:
                    # This is a query that modifies data
                    logger.debug("Query modifies data")
                    await conn.commit()
                    result = {
                        "status": "success",
                        "affected_rows": cursor.rowcount,
                        "execution_time": (datetime.now() - start_time).total_seconds()
                    }
                    logger.info(f"Query affected {cursor.rowcount} rows")
            
            await self.connection_manager.release_connection(server_id, conn)
            return result
            
        except Exception as e:
            logger.error(f"Failed to execute query: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status"""
        logger.debug("_get_connection_status called")
        active_connections = []
        
        for session, (server_id, database) in self.connection_manager.active_connections.items():
            active_connections.append({
                "session_id": session,
                "server_id": server_id,
                "database": database,
                "server_alias": self.connection_manager.servers[server_id].get("alias", server_id)
            })
        
        pool_status = []
        for server_id, pool in self.connection_manager.pools.items():
            pool_status.append({
                "server_id": server_id,
                "size": pool.size,
                "freesize": pool.freesize,
                "maxsize": pool.maxsize
            })
        
        result = {
            "current_session": self.current_session,
            "active_connections": active_connections,
            "connection_pools": pool_status
        }
        logger.debug(f"Status: {len(active_connections)} active connections, {len(pool_status)} pools")
        return result
    
    async def run(self):
        """Run the MCP server"""
        logger.info("=== Starting MCP server run method ===")
        try:
            # Load configuration
            await self.connection_manager.load_config(self.config_path)
            
            # Run the server
            async with stdio_server() as (read_stream, write_stream):
                logger.info("Starting server with stdio transport")
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="mysql-mcp-server",
                        server_version="1.0.0",
                        capabilities={
                            "tools": {},
                            "resources": None,
                            "prompts": None
                        }
                    )
                )
                logger.info("Server run completed")
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
            raise
        finally:
            logger.info("Cleaning up...")
            await self.connection_manager.close_all()
            logger.info("Server shutdown complete")

# Entry point
async def main():
    logger.info("=== Secure MySQL MCP Starting ===")
    
    # Check for help flag
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help', 'help']:
        print_usage()
        return
    
    # Check if config path is provided
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/servers.json"
    
    # Convert to Path object for easier handling
    config_path = Path(config_path)
    
    # Check if config file exists
    if not config_path.exists():
        logger.warning(f"Configuration file {config_path} does not exist")
        
        # Create parent directory if it doesn't exist
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create a sample configuration file
        sample_config = {
            "servers": [
                {
                    "id": "local-mysql",
                    "alias": "Local MySQL Server",
                    "host": "localhost",
                    "port": 3306,
                    "user": "root",
                    "password": "your_password_here",
                    "default_database": "mysql",
                    "enabled": True,
                    "encrypted": False,
                    "max_connections": 5,
                    "connection_timeout": 10,
                    "permissions": []
                }
            ]
        }
        
        with open(config_path, 'w') as f:
            json.dump(sample_config, f, indent=2)
        
        logger.info(f"Created sample configuration at {config_path}")
        logger.info("Please update the configuration with your MySQL server details")
        return
    
    # Run server with the specified config
    logger.info(f"Using configuration file: {config_path}")
    server = MySQLMCPServer(str(config_path))
    await server.run()
    logger.info("=== Secure MySQL MCP Stopped ===")

if __name__ == "__main__":
    asyncio.run(main())
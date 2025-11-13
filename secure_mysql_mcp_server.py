import argparse
import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiomysql
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastmcp.server import Context, FastMCP
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Setup logging with more detail
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger(__name__)
load_dotenv()

SERVER_INSTRUCTIONS = """Secure MySQL MCP server that exposes list/connect/disconnect/execute/status tools for
pre-configured MySQL instances. Connections are pooled, passwords can be encrypted, and READ_ONLY
servers enforce guard rails. Configure servers in config/servers.json before launching."""

DEFAULT_HTTP_PATH = "/mcp"
COMMENT_PREFIX_PATTERN = re.compile(r"^\s*(?:--[^\n]*\n|#[^\n]*\n|/\*.*?\*/\s*)*", re.DOTALL)
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_PATH = LOG_DIR / "sql_audit.log"
AUDIT_LOGGER = logging.getLogger("sql_audit")
AUDIT_LOGGER.setLevel(logging.INFO)
AUDIT_LOGGER.propagate = False
if not AUDIT_LOGGER.handlers:
    audit_handler = logging.FileHandler(AUDIT_LOG_PATH)
    audit_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    AUDIT_LOGGER.addHandler(audit_handler)
AUTH_TOKEN_VAR: ContextVar[Optional[str]] = ContextVar("mcp_auth_token", default=None)


class BearerTokenAuthMiddleware(BaseHTTPMiddleware):
    """Simple bearer token middleware for FastMCP HTTP endpoints."""

    def __init__(self, app, *, valid_tokens: Set[str]):
        super().__init__(app)
        self.valid_tokens = valid_tokens

    async def dispatch(self, request, call_next):
        # If no tokens are configured, skip auth
        if not self.valid_tokens:
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )

        token = auth_header.split(" ", 1)[1].strip()
        if token not in self.valid_tokens:
            return JSONResponse({"error": "Unauthorized"}, status_code=403)

        reset_token = AUTH_TOKEN_VAR.set(token)
        try:
            return await call_next(request)
        finally:
            AUTH_TOKEN_VAR.reset(reset_token)


class PasswordManager:
    """Simple password encryption/decryption manager"""

    def __init__(self, key: Optional[bytes] = None):
        logger.debug("Initializing PasswordManager")
        if key:
            if isinstance(key, bytes) and len(key) == 44:
                self.cipher = Fernet(key)
            else:
                try:
                    self.cipher = Fernet(key)
                except Exception:  # pragma: no cover - Fernet raises many variants
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
        self.active_connections: Dict[str, Tuple[str, str]] = {}
        self.password_manager = PasswordManager()
        logger.debug("ConnectionManager initialized")

    async def load_config(self, config_path: str):
        """Load server configurations from JSON file"""
        logger.info(f"Loading configuration from {config_path}")
        try:
            with open(config_path, "r") as f:
                config = json.load(f)

            if "encryption_key" in config:
                logger.debug("Found encryption key in config")
                self.password_manager = PasswordManager(config["encryption_key"].encode("utf-8"))

            self.servers = {}
            enabled_count = 0
            disabled_count = 0

            for server in config.get("servers", []):
                if server.get("enabled", True):
                    self.servers[server["id"]] = server
                    enabled_count += 1
                    logger.debug(f"Loaded server: {server['id']} ({server.get('alias', 'N/A')})")
                else:
                    disabled_count += 1
                    logger.info(
                        f"Skipping disabled server: {server['id']} ({server.get('alias', 'N/A')})"
                    )

            logger.info(
                f"Loaded {enabled_count} enabled server(s), skipped {disabled_count} disabled server(s)"
            )
        except Exception as exc:
            logger.error(f"Failed to load config: {exc}")
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
        password = server["password"]
        if server.get("encrypted", True):
            try:
                password = self.password_manager.decrypt(password)
                logger.debug(f"Password decrypted for {server_id}")
            except Exception:
                logger.warning(f"Failed to decrypt password for {server_id}, using as plain text")

        pool_params = {
            "host": server["host"],
            "port": server.get("port", 3306),
            "user": server["user"],
            "password": password,
            "minsize": 1,
            "maxsize": server.get("max_connections", 5),
            "connect_timeout": server.get("connection_timeout", 10),
            "echo": False,
        }

        if "default_database" in server:
            pool_params["db"] = server["default_database"]
            logger.debug(f"Using default database: {server['default_database']}")

        pool = await aiomysql.create_pool(**pool_params)
        self.pools[server_id] = pool
        logger.info(f"Created connection pool for {server_id}")
        return pool

    async def get_connection(self, server_id: str, database: Optional[str] = None) -> aiomysql.Connection:
        """Get a connection from the pool"""
        pool = await self.create_pool(server_id)
        conn = await pool.acquire()

        if database:
            await conn.select_db(database)
            logger.debug(f"Selected database: {database}")

        return conn

    async def release_connection(self, server_id: str, conn: aiomysql.Connection):
        """Release connection back to pool"""
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


class SecureMySQLFastMCPServer:
    """FastMCP-powered Secure MySQL server"""

    def __init__(self, config_path: str, http_path: str = DEFAULT_HTTP_PATH):
        self.config_path = config_path
        self.http_path = http_path
        self.connection_manager = ConnectionManager()
        self.auth_tokens: Set[str] = set()
        self._refresh_auth_tokens()
        self.app = FastMCP(
            name="secure-mysql-mcp",
            instructions=SERVER_INSTRUCTIONS,
            lifespan=self._lifespan,
        )
        self._register_tools()

    @asynccontextmanager
    async def _lifespan(self, _: FastMCP):
        await self.connection_manager.load_config(self.config_path)
        self._refresh_auth_tokens()
        try:
            yield
        finally:
            await self.connection_manager.close_all()

    def _register_tools(self) -> None:
        logger.info("Registering FastMCP tools")

        @self.app.tool(
            name="list_available_databases",
            description="List configured MySQL servers and their databases",
            tags={"mysql", "metadata"},
        )
        async def list_available_databases() -> Dict[str, Any]:
            result = await self._list_available_databases()
            self._log_tool_result("list_available_databases", result)
            return result

        @self.app.tool(
            name="connect_database",
            description="Connect the current MCP session to a MySQL database",
            tags={"mysql", "connection"},
        )
        async def connect_database(server_id: str, database: str, ctx: Context) -> Dict[str, Any]:
            session_id = ctx.session_id
            result = await self._connect_database(session_id, server_id, database)
            self._log_tool_result("connect_database", result)
            return result

        @self.app.tool(
            name="disconnect_database",
            description="Disconnect the current MCP session from MySQL",
            tags={"mysql", "connection"},
        )
        async def disconnect_database(ctx: Context, server_id: Optional[str] = None) -> Dict[str, Any]:
            result = await self._disconnect_database(ctx.session_id, server_id)
            self._log_tool_result("disconnect_database", result)
            return result

        @self.app.tool(
            name="execute_sql",
            description="Execute a SQL query using the session's connection",
            tags={"mysql", "query"},
        )
        async def execute_sql(
            ctx: Context,
            query: str,
            server_id: Optional[str] = None,
            database: Optional[str] = None,
        ) -> Dict[str, Any]:
            session_id = ctx.session_id
            result = await self._execute_sql(session_id, query, server_id, database)
            self._log_tool_result("execute_sql", result)
            return result

        @self.app.tool(
            name="get_connection_status",
            description="Inspect connection pools and active sessions",
            tags={"mysql", "status"},
        )
        async def get_connection_status(ctx: Context) -> Dict[str, Any]:
            result = await self._get_connection_status(ctx.session_id)
            self._log_tool_result("get_connection_status", result)
            return result

    def _log_tool_result(self, tool_name: str, result: Any) -> None:
        safe_result = self._sanitize_result_for_logging(result)
        logger.debug(
            "Tool %s result: %s",
            tool_name,
            json.dumps(safe_result, indent=2, default=str) if isinstance(safe_result, dict) else safe_result,
        )

    def _sanitize_result_for_logging(self, result: Any) -> Any:
        if not isinstance(result, dict):
            return result

        sanitized = result.copy()
        if "servers" in sanitized:
            sanitized_servers = []
            for server in sanitized["servers"]:
                safe_server = server.copy()
                safe_server.pop("password", None)
                safe_server.pop("user", None)
                safe_server.pop("host", None)
                safe_server.pop("port", None)
                sanitized_servers.append(safe_server)
            sanitized["servers"] = sanitized_servers

        if "rows" in sanitized and isinstance(sanitized["rows"], list) and len(sanitized["rows"]) > 5:
            sanitized["rows"] = sanitized["rows"][:5] + [
                {"...": f"and {len(sanitized['rows']) - 5} more rows"}
            ]

        return sanitized

    def _refresh_auth_tokens(self) -> None:
        """Load bearer tokens from config and environment."""
        tokens: Set[str] = set()

        def _split_token_blob(blob: str) -> List[str]:
            parts = re.split(r"[,\n]", blob)
            return [p.strip() for p in parts if p.strip()]

        env_multi = os.getenv("MCP_BEARER_TOKENS")
        if env_multi:
            tokens.update(_split_token_blob(env_multi))

        env_single = os.getenv("MCP_BEARER_TOKEN")
        if env_single:
            tokens.add(env_single.strip())

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
            auth_section = data.get("auth", {})
            config_tokens = auth_section.get("tokens", [])
            if isinstance(config_tokens, list):
                tokens.update(token.strip() for token in config_tokens if isinstance(token, str) and token.strip())
        except FileNotFoundError:
            logger.debug("Config file %s not found when loading auth tokens", self.config_path)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse auth tokens from %s: %s", self.config_path, exc)

        self.auth_tokens = tokens
        if tokens:
            logger.info("Loaded %d bearer token(s) for HTTP authentication", len(tokens))
        else:
            logger.warning("No bearer tokens configured; HTTP endpoint is unauthenticated")

    def _audit_sql(
        self,
        *,
        session_id: str,
        server_id: Optional[str],
        database: Optional[str],
        query: str,
        status: str,
        execution_time: Optional[float] = None,
        row_count: Optional[int] = None,
        affected_rows: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        payload = {
            "session_id": session_id,
            "server_id": server_id,
            "database": database,
            "status": status,
            "query": query.strip(),
            "execution_time": execution_time,
            "row_count": row_count,
            "affected_rows": affected_rows,
            "error": error,
            "token_prefix": None,
        }
        token = AUTH_TOKEN_VAR.get()
        if token:
            payload["token_prefix"] = token[:6]
        AUDIT_LOGGER.info(json.dumps(payload, ensure_ascii=False))

    def _build_middleware(self) -> List[StarletteMiddleware]:
        middleware: List[StarletteMiddleware] = []
        if self.auth_tokens:
            middleware.append(
                StarletteMiddleware(BearerTokenAuthMiddleware, valid_tokens=self.auth_tokens)
            )
        return middleware

    @staticmethod
    def _extract_first_keyword(query: str) -> str:
        """Return the leading SQL keyword, ignoring whitespace and comments."""
        if not query:
            return ""

        upper_query = query.upper()
        match = COMMENT_PREFIX_PATTERN.match(upper_query)
        start_index = match.end() if match else 0
        remainder = upper_query[start_index:].lstrip()
        token_match = re.match(r"[A-Z_]+", remainder)
        return token_match.group(0) if token_match else ""

    async def _list_available_databases(self) -> Dict[str, Any]:
        result = {"servers": [], "summary": {"total": 0, "enabled": 0, "connected": 0, "errors": 0}}

        for server_id, server_config in self.connection_manager.servers.items():
            if not server_config.get("enabled", True):
                continue

            result["summary"]["total"] += 1
            result["summary"]["enabled"] += 1

            server_info = {
                "id": server_id,
                "alias": server_config.get("alias", server_id),
                "status": "available",
                "default_database": server_config.get("default_database"),
                "databases": [],
                "permissions": server_config.get("permissions", []),
            }

            try:
                conn = await self.connection_manager.get_connection(server_id)
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SHOW DATABASES")
                        databases = await cursor.fetchall()
                        server_info["databases"] = [db[0] for db in databases]
                        server_info["status"] = "connected"
                        result["summary"]["connected"] += 1
                finally:
                    await self.connection_manager.release_connection(server_id, conn)
            except Exception as exc:
                server_info["status"] = "error"
                server_info["error"] = str(exc)
                result["summary"]["errors"] += 1
                logger.error(f"Failed to list databases for {server_id}: {exc}")

            result["servers"].append(server_info)

        return result

    async def _connect_database(self, session_id: str, server_id: str, database: str) -> Dict[str, Any]:
        logger.debug(f"_connect_database called: {session_id} -> {server_id}/{database}")
        try:
            conn = await self.connection_manager.get_connection(server_id, database)
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT DATABASE()")
                    current_db = await cursor.fetchone()
                    await cursor.execute("SELECT VERSION()")
                    mysql_version = await cursor.fetchone()

                self.connection_manager.active_connections[session_id] = (server_id, database)
                logger.debug(f"Stored active connection for session {session_id}")

                return {
                    "status": "connected",
                    "session_id": session_id,
                    "server_id": server_id,
                    "database": database,
                    "current_database": current_db[0] if current_db else database,
                    "mysql_version": mysql_version[0] if mysql_version else "unknown",
                }
            finally:
                await self.connection_manager.release_connection(server_id, conn)
        except Exception as exc:
            logger.error(f"Failed to connect to {server_id}/{database}: {exc}")
            return {"status": "error", "error": str(exc)}

    async def _disconnect_database(self, session_id: str, server_id: Optional[str]) -> Dict[str, Any]:
        logger.debug(f"_disconnect_database called: session={session_id}, server_id={server_id}")
        session_entry = self.connection_manager.active_connections.get(session_id)
        if not session_entry:
            return {
                "status": "not_connected",
                "session_id": session_id,
                "message": "No active connection for this session",
            }

        active_server, _ = session_entry
        if server_id and active_server != server_id:
            return {
                "status": "not_connected",
                "session_id": session_id,
                "message": f"Session connected to {active_server}, not {server_id}",
            }

        del self.connection_manager.active_connections[session_id]
        logger.info(f"Disconnected session {session_id}")
        return {"status": "disconnected", "session_id": session_id}

    async def _execute_sql(
        self,
        session_id: str,
        query: str,
        server_id: Optional[str],
        database: Optional[str],
    ) -> Dict[str, Any]:
        query_preview = query[:30] + "..." if len(query) > 30 else query
        logger.debug(
            f"_execute_sql called: session={session_id}, query='{query_preview}', server_id={server_id}, database={database}"
        )

        resolved_server = server_id
        resolved_database = database

        if not resolved_server and session_id in self.connection_manager.active_connections:
            resolved_server, resolved_database = self.connection_manager.active_connections[session_id]
            logger.debug(f"Using session-bound connection: {resolved_server}/{resolved_database}")
        elif not resolved_server:
            for sid, server in self.connection_manager.servers.items():
                if server.get("default_database"):
                    resolved_server = sid
                    resolved_database = server["default_database"]
                    logger.debug(f"Using default database: {resolved_server}/{resolved_database}")
                    break

        if not resolved_server:
            return {"error": "No active connection and no default database configured. Please connect first."}

        if not resolved_database and resolved_server in self.connection_manager.servers:
            resolved_database = self.connection_manager.servers[resolved_server].get("default_database")

        if not resolved_database:
            return {"error": f"No database specified for server {resolved_server}. Please connect first."}

        server_permissions = self.connection_manager.servers.get(resolved_server, {}).get("permissions", [])
        if "READ_ONLY" in server_permissions:
            dangerous_keywords = {
                "DROP",
                "DELETE",
                "UPDATE",
                "INSERT",
                "CREATE",
                "ALTER",
                "TRUNCATE",
                "GRANT",
                "REVOKE",
            }
            first_keyword = self._extract_first_keyword(query)
            if first_keyword in dangerous_keywords:
                reason = f"Query starts with restricted keyword '{first_keyword}' - server {resolved_server} is READ_ONLY"
                self._audit_sql(
                    session_id=session_id,
                    server_id=resolved_server,
                    database=resolved_database,
                    query=query,
                    status="blocked",
                    error=reason,
                )
                return {"error": reason}

        try:
            start_time = datetime.now()
            conn = await self.connection_manager.get_connection(resolved_server, resolved_database)
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(query)
                    query_upper = query.strip().upper()
                    is_result_query = any(
                        query_upper.startswith(cmd)
                        for cmd in ["SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"]
                    )

                    if is_result_query and cursor.description:
                        rows_raw = await cursor.fetchall()
                        columns = [desc[0] for desc in cursor.description]
                        rows: List[Dict[str, Any]] = []
                        for row in rows_raw:
                            row_dict: Dict[str, Any] = {}
                            for idx, value in enumerate(row):
                                row_dict[columns[idx]] = value
                            rows.append(row_dict)

                        result = {
                            "status": "success",
                            "columns": columns,
                            "rows": rows,
                            "row_count": len(rows),
                            "execution_time": (datetime.now() - start_time).total_seconds(),
                        }
                        self._audit_sql(
                            session_id=session_id,
                            server_id=resolved_server,
                            database=resolved_database,
                            query=query,
                            status="success",
                            execution_time=result["execution_time"],
                            row_count=result["row_count"],
                        )
                    else:
                        await conn.commit()
                        result = {
                            "status": "success",
                            "affected_rows": cursor.rowcount,
                            "execution_time": (datetime.now() - start_time).total_seconds(),
                        }
                        self._audit_sql(
                            session_id=session_id,
                            server_id=resolved_server,
                            database=resolved_database,
                            query=query,
                            status="success",
                            execution_time=result["execution_time"],
                            affected_rows=result["affected_rows"],
                        )
            finally:
                await self.connection_manager.release_connection(resolved_server, conn)
            return result
        except Exception as exc:
            logger.error(f"Failed to execute query: {exc}", exc_info=True)
            self._audit_sql(
                session_id=session_id,
                server_id=resolved_server,
                database=resolved_database,
                query=query,
                status="error",
                error=str(exc),
            )
            return {"status": "error", "error": str(exc)}

    async def _get_connection_status(self, session_id: Optional[str]) -> Dict[str, Any]:
        active_connections = []
        for session, (server_id, database) in self.connection_manager.active_connections.items():
            server_alias = self.connection_manager.servers.get(server_id, {}).get("alias", server_id)
            active_connections.append(
                {
                    "session_id": session,
                    "server_id": server_id,
                    "database": database,
                    "server_alias": server_alias,
                }
            )

        pool_status = []
        for server_id, pool in self.connection_manager.pools.items():
            pool_status.append(
                {
                    "server_id": server_id,
                    "size": pool.size,
                    "freesize": pool.freesize,
                    "maxsize": pool.maxsize,
                }
            )

        session_info = None
        if session_id and session_id in self.connection_manager.active_connections:
            server_id, database = self.connection_manager.active_connections[session_id]
            session_info = {
                "session_id": session_id,
                "server_id": server_id,
                "database": database,
                "server_alias": self.connection_manager.servers.get(server_id, {}).get("alias", server_id),
            }

        return {
            "session": session_info,
            "active_connections": active_connections,
            "connection_pools": pool_status,
        }

    async def run(
        self,
        *,
        host: str,
        port: int,
        transport: str = "streamable-http",
        path: Optional[str] = None,
    ) -> None:
        self._refresh_auth_tokens()
        logger.info(f"Starting FastMCP server on http://{host}:{port}{path or self.http_path}")
        await self.app.run_http_async(
            transport=transport,
            host=host,
            port=port,
            path=path or self.http_path,
            middleware=self._build_middleware(),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Secure MySQL MCP powered by FastMCP")
    parser.add_argument(
        "config",
        nargs="?",
        default="config/servers.json",
        help="Path to the server configuration JSON",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="HTTP port to bind (default: 8090)")
    parser.add_argument(
        "--path",
        default=DEFAULT_HTTP_PATH,
        help="HTTP path for the MCP endpoint (default: /mcp)",
    )
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "http", "sse"],
        default="streamable-http",
        help="MCP transport to expose (default: streamable-http)",
    )
    return parser


def ensure_config_exists(config_path: Path) -> bool:
    if config_path.exists():
        return True

    logger.warning(f"Configuration file {config_path} does not exist; creating example")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    sample_config = {
        "auth": {
            "tokens": [
                "replace-with-secure-token"
            ]
        },
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
                "permissions": [],
            }
        ]
    }
    with open(config_path, "w") as f:
        json.dump(sample_config, f, indent=2)
    logger.info(f"Created sample configuration at {config_path}; please update it before restarting")
    return False


async def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    config_path = Path(args.config)

    if not ensure_config_exists(config_path):
        return

    server = SecureMySQLFastMCPServer(str(config_path), http_path=args.path)
    await server.run(host=args.host, port=args.port, transport=args.transport, path=args.path)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Secure MySQL MCP Test Client (FastMCP HTTP)
Connects to the Secure MySQL FastMCP server over streamable HTTP.
"""

import argparse
import asyncio
import json
from typing import Any, Dict, Optional

from fastmcp import Client
from fastmcp.exceptions import ToolError


class MySQLMCPTestClient:
    """Interactive test client for Secure MySQL MCP"""

    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url
        self.client = Client(base_url, auth=token) if token else Client(base_url)
        self.tools: Dict[str, Any] = {}

    async def run(self):
        print(f"Connecting to Secure MySQL MCP at {self.base_url} ...")
        async with self.client:
            tool_list = await self.client.list_tools()
            self.tools = {tool.name: tool for tool in tool_list}
            print(f"Connected! Available tools: {list(self.tools.keys())}")
            await self.interactive_mode()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")

        try:
            result = await self.client.call_tool(tool_name, arguments)
        except ToolError as exc:
            raise RuntimeError(str(exc)) from exc

        if result.data is not None:
            return result.data  # FastMCP parsed structured data
        if result.structured_content:
            return result.structured_content
        for block in result.content:
            if getattr(block, "type", "") == "text":
                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    return {"status": "error", "error": block.text}
        return {}

    async def cmd_tools(self):
        print("\nAvailable MCP Tools:")
        if not self.tools:
            print("  No tools loaded!")
            return

        for name, tool in self.tools.items():
            print(f"\n  Tool: {name}")
            print(f"    Description: {tool.description}")
            schema = getattr(tool, "inputSchema", None)
            if schema and schema.get("properties"):
                print("    Parameters:")
                for param, details in schema["properties"].items():
                    required = param in schema.get("required", [])
                    req_text = " (required)" if required else " (optional)"
                    print(f"      - {param}: {details.get('type', 'any')}{req_text}")
                    if "description" in details:
                        print(f"        {details['description']}")

    async def interactive_mode(self):
        print("\n=== Secure MySQL MCP Test Client ===")
        print("Type 'help' for commands, 'quit' to exit\n")
        print(f"Connected to MCP server with {len(self.tools)} tools available")

        while True:
            try:
                command = input("> ").strip()

                if command == "quit":
                    break
                if command == "help":
                    self.print_help()
                elif command == "tools":
                    await self.cmd_tools()
                elif command == "list":
                    await self.cmd_list_databases()
                elif command.startswith("connect"):
                    await self.cmd_connect(command)
                elif command == "disconnect":
                    await self.cmd_disconnect()
                elif command.startswith("sql"):
                    await self.cmd_execute_sql(command)
                elif command == "status":
                    await self.cmd_status()
                elif command == "test":
                    await self.run_automated_tests()
                else:
                    print(f"Unknown command: {command}")
            except KeyboardInterrupt:
                print("\nUse 'quit' to exit")
            except Exception as exc:
                print(f"Error: {exc}")

    def print_help(self):
        print(
            """
Available commands:
  help                - Show this help message
  tools               - Show all available MCP tools
  list                - List available databases
  connect <server_id> <database> - Connect to a database
  disconnect          - Disconnect from current database
  sql <query>         - Execute SQL query
  status              - Show connection status
  test                - Run automated tests
  quit                - Exit the client
            """
        )

    async def cmd_list_databases(self):
        result = await self.call_tool("list_available_databases", {})

        print("\nAvailable MySQL Servers:")
        summary = result.get("summary")
        if summary:
            print(
                f"\nSummary: {summary['enabled']} enabled, {summary['connected']} connected, {summary['errors']} errors"
            )

        for server in result.get("servers", []):
            print(f"\n  Server: {server['id']} ({server['alias']})")
            print(f"    Status: {server['status']}")
            if server.get("default_database"):
                print(f"    Default DB: {server['default_database']}")
            if server.get("permissions"):
                print(f"    Permissions: {', '.join(server['permissions'])}")
            if server.get("databases"):
                sample = ", ".join(server["databases"][:5])
                print(f"    Databases: {sample}")
                if len(server["databases"]) > 5:
                    print(f"              ... and {len(server['databases']) - 5} more")
            if "error" in server:
                print(f"    Error: {server['error']}")

    async def cmd_connect(self, command: str):
        parts = command.split()
        if len(parts) != 3:
            print("Usage: connect <server_id> <database>")
            return

        server_id, database = parts[1], parts[2]
        result = await self.call_tool(
            "connect_database", {"server_id": server_id, "database": database}
        )

        if result.get("status") == "connected":
            print(f"Connected to {server_id}/{database}")
            print(f"MySQL Version: {result.get('mysql_version')}")
        else:
            print(f"Connection failed: {result.get('error')}")

    async def cmd_disconnect(self):
        result = await self.call_tool("disconnect_database", {})
        print(f"Disconnection status: {result.get('status')} ({result.get('message', 'ok')})")

    async def cmd_execute_sql(self, command: str):
        if not command.startswith("sql "):
            print("Usage: sql <query>")
            return

        query = command[4:].strip()
        if not query:
            print("No query provided")
            return

        print(f"Executing: {query}")
        result = await self.call_tool("execute_sql", {"query": query})

        if result.get("status") != "success":
            print(f"Query failed: {result.get('error')}")
            return

        if "rows" in result:
            print(f"\nQuery executed successfully ({result['execution_time']:.3f}s)")
            print(f"Returned {result['row_count']} rows")
            columns = result.get("columns", [])
            if columns:
                print("\n" + " | ".join(columns))
                print("-" * (sum(len(col) for col in columns) + 3 * (len(columns) - 1)))
                for row in result["rows"][:10]:
                    values = [str(row.get(col, "")) for col in columns]
                    print(" | ".join(values))
                if result["row_count"] > 10:
                    print(f"\n... and {result['row_count'] - 10} more rows")
        else:
            print(f"\nQuery executed successfully ({result['execution_time']:.3f}s)")
            print(f"Affected rows: {result.get('affected_rows', 0)}")

    async def cmd_status(self):
        result = await self.call_tool("get_connection_status", {})

        print("\nConnection Status:")
        session = result.get("session")
        if session:
            print(
                f"  This session: {session['session_id']} -> {session['server_id']}/{session['database']} ({session['server_alias']})"
            )
        else:
            print("  This session has no active connection")

        active = result.get("active_connections", [])
        if active:
            print("\n  Active connections:")
            for conn in active:
                print(
                    f"    - {conn['session_id']}: {conn['server_id']}/{conn['database']} ({conn['server_alias']})"
                )
        else:
            print("\n  No active connections across sessions")

        pools = result.get("connection_pools", [])
        if pools:
            print("\n  Connection pools:")
            for pool in pools:
                print(
                    f"    - {pool['server_id']}: {pool['freesize']}/{pool['size']} free (max: {pool['maxsize']})"
                )
        else:
            print("\n  No pools have been created yet")

    async def run_automated_tests(self):
        print("\n=== Running Automated Tests ===\n")
        tests_passed = 0
        tests_failed = 0

        async def record(test_name: str, coro) -> None:
            nonlocal tests_passed, tests_failed
            print(f"{test_name}")
            try:
                await coro
                print("  ✓ Passed")
                tests_passed += 1
            except Exception as exc:
                print(f"  ✗ Failed: {exc}")
                tests_failed += 1

        await record("Test 0: Tools available", self.cmd_tools())

        async def test_list():
            result = await self.call_tool("list_available_databases", {})
            assert "servers" in result

        await record("Test 1: List available databases", test_list())

        async def test_connect():
            result = await self.call_tool("list_available_databases", {})
            if not result.get("servers"):
                raise RuntimeError("No servers configured")
            server = result["servers"][0]
            if not server.get("databases"):
                raise RuntimeError("Server has no databases to test")
            db = server["databases"][0]
            connect_result = await self.call_tool(
                "connect_database", {"server_id": server["id"], "database": db}
            )
            assert connect_result.get("status") == "connected"

        await record("Test 2: Connect to database", test_connect())

        async def test_query():
            result = await self.call_tool("execute_sql", {"query": "SELECT 1 as test_column"})
            assert result.get("status") == "success"

        await record("Test 3: Execute SQL", test_query())

        async def test_status():
            result = await self.call_tool("get_connection_status", {})
            assert "active_connections" in result

        await record("Test 4: Connection status", test_status())

        async def test_disconnect():
            result = await self.call_tool("disconnect_database", {})
            assert "status" in result

        await record("Test 5: Disconnect", test_disconnect())

        async def test_default_db():
            list_result = await self.call_tool("list_available_databases", {})
            default_server: Optional[Dict[str, Any]] = None
            for server in list_result.get("servers", []):
                if server.get("default_database"):
                    default_server = server
                    break
            if not default_server:
                print("  ⚠ Skipped: No server with default database")
                return
            await self.call_tool("disconnect_database", {})
            result = await self.call_tool("execute_sql", {"query": "SELECT DATABASE() as current_db"})
            assert result.get("status") == "success"

        await record("Test 6: Default database execution", test_default_db())

        print("\n=== Test Results ===")
        print(f"Passed: {tests_passed}")
        print(f"Failed: {tests_failed}")
        print(f"Total: {tests_passed + tests_failed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test client for the Secure MySQL FastMCP server")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8090/mcp",
        help="Streamable HTTP endpoint for the MCP server (default: http://127.0.0.1:8090/mcp)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token to send in Authorization header",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    client = MySQLMCPTestClient(args.url, args.token)

    try:
        await client.run()
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    asyncio.run(main())

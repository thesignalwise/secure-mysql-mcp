#!/usr/bin/env python3
"""
Secure MySQL MCP Test Client
This provides an interactive test client for the Secure MySQL MCP
"""

import asyncio
import json
import sys
from typing import Dict, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MySQLMCPTestClient:
    """Interactive test client for Secure MySQL MCP"""
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.tools: Dict[str, Any] = {}
        self.server_script_path: str = ""
    
    async def run_with_server(self, server_script_path: str = "secure_mysql_mcp_server.py"):
        """Run the client with the server"""
        self.server_script_path = server_script_path
        print(f"Starting Secure MySQL MCP from {server_script_path}...")
        
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[server_script_path],
            env=None
        )
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                self.session = session
                
                # Initialize session
                await session.initialize()
                
                # Get available tools
                tools_response = await session.list_tools()
                self.tools = {tool.name: tool for tool in tools_response.tools}
                
                print(f"Connected! Available tools: {list(self.tools.keys())}")
                
                # Run interactive mode
                await self.interactive_mode()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the server"""
        if not self.session:
            raise RuntimeError("Not connected to server")
        
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        result = await self.session.call_tool(tool_name, arguments)
        return json.loads(result.content[0].text)
    
    async def cmd_tools(self):
        """Show available tools"""
        print("\nAvailable MCP Tools:")
        if not self.tools:
            print("  No tools loaded!")
            return
        
        for name, tool in self.tools.items():
            print(f"\n  Tool: {name}")
            print(f"    Description: {tool.description}")
            if hasattr(tool, 'inputSchema'):
                schema = tool.inputSchema
                if schema.get('properties'):
                    print("    Parameters:")
                    for param, details in schema['properties'].items():
                        required = param in schema.get('required', [])
                        req_text = " (required)" if required else " (optional)"
                        print(f"      - {param}: {details.get('type', 'any')}{req_text}")
                        if 'description' in details:
                            print(f"        {details['description']}")
    
    async def interactive_mode(self):
        """Run interactive test mode"""
        print("\n=== Secure MySQL MCP Test Client ===")
        print("Type 'help' for available commands, 'quit' to exit\n")
        
        # Show initial status
        print(f"Connected to MCP server with {len(self.tools)} tools available")
        
        while True:
            try:
                command = input("> ").strip()
                
                if command == "quit":
                    break
                elif command == "help":
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
                    print("Type 'help' for available commands")
            
            except KeyboardInterrupt:
                print("\nUse 'quit' to exit")
            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
    
    def print_help(self):
        """Print help information"""
        print("""
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
        """)
    
    async def cmd_list_databases(self):
        """List available databases"""
        result = await self.call_tool("list_available_databases", {})
        
        print("\nAvailable MySQL Servers:")
        
        # Show summary if available
        if "summary" in result:
            summary = result["summary"]
            print(f"\nSummary: {summary['enabled']} enabled, {summary['connected']} connected, {summary['errors']} errors")
        
        for server in result.get("servers", []):
            print(f"\n  Server: {server['id']} ({server['alias']})")
            # Note: Host/port info is not returned for security reasons
            print(f"    Status: {server['status']}")
            if server.get('default_database'):
                print(f"    Default DB: {server['default_database']}")
            if server.get('permissions'):
                print(f"    Permissions: {', '.join(server['permissions'])}")
            if server.get('databases'):
                print(f"    Databases: {', '.join(server['databases'][:5])}")
                if len(server['databases']) > 5:
                    print(f"                ... and {len(server['databases']) - 5} more")
            if 'error' in server:
                print(f"    Error: {server['error']}")
    
    async def cmd_connect(self, command: str):
        """Connect to a database"""
        parts = command.split()
        if len(parts) != 3:
            print("Usage: connect <server_id> <database>")
            return
        
        server_id = parts[1]
        database = parts[2]
        
        result = await self.call_tool("connect_database", {
            "server_id": server_id,
            "database": database
        })
        
        if result.get("status") == "connected":
            print(f"Connected to {server_id}/{database}")
            print(f"MySQL Version: {result.get('mysql_version')}")
        else:
            print(f"Connection failed: {result.get('error')}")
    
    async def cmd_disconnect(self):
        """Disconnect from database"""
        result = await self.call_tool("disconnect_database", {})
        print(f"Disconnection status: {result.get('status')}")
    
    async def cmd_execute_sql(self, command: str):
        """Execute SQL query"""
        # Extract SQL query from command
        if not command.startswith("sql "):
            print("Usage: sql <query>")
            return
        
        query = command[4:].strip()
        if not query:
            print("No query provided")
            return
        
        print(f"Executing: {query}")
        result = await self.call_tool("execute_sql", {"query": query})
        
        if result.get("status") == "success":
            if "rows" in result:
                # SELECT query
                print(f"\nQuery executed successfully ({result['execution_time']:.3f}s)")
                print(f"Returned {result['row_count']} rows")
                
                if result['rows']:
                    # Print table header
                    columns = result['columns']
                    print("\n" + " | ".join(columns))
                    print("-" * (sum(len(col) for col in columns) + 3 * (len(columns) - 1)))
                    
                    # Print rows
                    for row in result['rows'][:10]:  # Limit to 10 rows for display
                        values = [str(row.get(col, '')) for col in columns]
                        print(" | ".join(values))
                    
                    if result['row_count'] > 10:
                        print(f"\n... and {result['row_count'] - 10} more rows")
            else:
                # Non-SELECT query
                print(f"\nQuery executed successfully ({result['execution_time']:.3f}s)")
                print(f"Affected rows: {result.get('affected_rows', 0)}")
        else:
            print(f"Query failed: {result.get('error')}")
    
    async def cmd_status(self):
        """Show connection status"""
        result = await self.call_tool("get_connection_status", {})
        
        print("\nConnection Status:")
        print(f"  Current session: {result.get('current_session', 'N/A')}")
        
        if result['active_connections']:
            print("\n  Active connections:")
            for conn in result['active_connections']:
                print(f"    - {conn['server_id']}/{conn['database']} ({conn['server_alias']})")
        else:
            print("\n  No active connections")
        
        if result['connection_pools']:
            print("\n  Connection pools:")
            for pool in result['connection_pools']:
                print(f"    - {pool['server_id']}: {pool['freesize']}/{pool['size']} free (max: {pool['maxsize']})")
    
    async def run_automated_tests(self):
        """Run automated test suite"""
        print("\n=== Running Automated Tests ===\n")
        
        tests_passed = 0
        tests_failed = 0
        
        # Test 0: Verify tools are loaded
        print("Test 0: Verify MCP tools are loaded")
        try:
            if not self.tools:
                print("  ⚠ No tools loaded - checking if server is properly initialized")
                # Wait a bit for initialization
                await asyncio.sleep(1)
            
            print(f"  Available tools: {list(self.tools.keys())}")
            assert len(self.tools) == 5, f"Expected 5 tools, got {len(self.tools)}"
            print("  ✓ Passed")
            tests_passed += 1
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
            print("  ⚠ Continuing with remaining tests...")
        
        # Test 1: List databases
        print("\nTest 1: List available databases")
        try:
            result = await self.call_tool("list_available_databases", {})
            assert "servers" in result
            assert "summary" in result
            print(f"  Found {result['summary']['enabled']} enabled servers")
            print("  ✓ Passed")
            tests_passed += 1
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        # Test 2: Connect to database
        print("\nTest 2: Connect to database")
        try:
            # Get first available server
            list_result = await self.call_tool("list_available_databases", {})
            if list_result["servers"] and list_result["servers"][0]["databases"]:
                server = list_result["servers"][0]
                database = server["databases"][0]
                
                result = await self.call_tool("connect_database", {
                    "server_id": server["id"],
                    "database": database
                })
                assert result.get("status") == "connected"
                print("  ✓ Passed")
                tests_passed += 1
            else:
                print("  ⚠ Skipped: No available databases")
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        # Test 3: Execute SQL query
        print("\nTest 3: Execute SQL query")
        try:
            result = await self.call_tool("execute_sql", {
                "query": "SELECT 1 as test_column"
            })
            assert result.get("status") == "success"
            assert result.get("rows") == [{"test_column": 1}]
            print("  ✓ Passed")
            tests_passed += 1
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        # Test 4: Get connection status
        print("\nTest 4: Get connection status")
        try:
            result = await self.call_tool("get_connection_status", {})
            assert "active_connections" in result
            assert "connection_pools" in result
            print("  ✓ Passed")
            tests_passed += 1
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        # Test 5: Disconnect
        print("\nTest 5: Disconnect from database")
        try:
            result = await self.call_tool("disconnect_database", {})
            assert "status" in result
            print("  ✓ Passed")
            tests_passed += 1
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        # Test 6: SQL with default database
        print("\nTest 6: SQL with default database (if configured)")
        try:
            # Check if any server has default database
            list_result = await self.call_tool("list_available_databases", {})
            default_db_server = None
            for server in list_result["servers"]:
                if server.get("default_database"):
                    default_db_server = server
                    break
            
            if default_db_server:
                # Disconnect first
                await self.call_tool("disconnect_database", {})
                
                # Try SQL without explicit connection
                result = await self.call_tool("execute_sql", {
                    "query": "SELECT DATABASE() as current_db"
                })
                
                if result.get("status") == "success":
                    print("  ✓ Passed - Default database used")
                    tests_passed += 1
                else:
                    print("  ℹ Default database not automatically used")
            else:
                print("  ⚠ Skipped: No server with default database")
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            tests_failed += 1
        
        print(f"\n=== Test Results ===")
        print(f"Passed: {tests_passed}")
        print(f"Failed: {tests_failed}")
        print(f"Total: {tests_passed + tests_failed}")

async def main():
    """Main entry point"""
    client = MySQLMCPTestClient()
    
    try:
        # Get server script path from command line or use default
        server_script = sys.argv[1] if len(sys.argv) > 1 else "secure_mysql_mcp_server.py"
        
        await client.run_with_server(server_script)
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
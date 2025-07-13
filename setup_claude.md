# Setting up Secure MySQL MCP with Claude Desktop

## Prerequisites

1. Claude Desktop installed
2. Python 3.8+ installed
3. MySQL server(s) accessible

## Step 1: Install the Secure MySQL MCP

1. Download and extract the secure-mysql-mcp.zip
2. Open a terminal in the extracted directory
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Step 2: Configure MySQL Servers

Edit `config/servers.json`:

```json
{
  "servers": [
    {
      "id": "local-mysql",
      "alias": "Local Development DB",
      "host": "localhost",
      "port": 3306,
      "user": "your_username",
      "password": "your_password",
      "encrypted": false
    }
  ]
}
```

## Step 3: Test the Server

Run the test client to verify everything works:

```bash
python test_client.py
```

Try these commands:
- `list` - Should show your MySQL server
- `test` - Runs automated tests

## Step 4: Configure Claude Desktop

1. Open Claude Desktop settings
2. Navigate to MCP Servers configuration
3. Add the following configuration:

### Windows
```json
{
  "mcpServers": {
    "mysql": {
      "command": "python",
      "args": ["C:\\path\\to\\secure_mysql_mcp_server.py"],
      "cwd": "C:\\path\\to\\secure-mysql-mcp"
    }
  }
}
```

### macOS/Linux
```json
{
  "mcpServers": {
    "mysql": {
      "command": "python",
      "args": ["/path/to/secure_mysql_mcp_server.py"],
      "cwd": "/path/to/secure-mysql-mcp"
    }
  }
}
```

## Step 5: Restart Claude Desktop

After saving the configuration, restart Claude Desktop for the changes to take effect.

## Step 6: Verify Integration

In Claude, you should now be able to:

1. Ask "What MySQL databases are available?"
2. Request "Connect to the local-mysql server and use the test database"
3. Run queries like "Show me all tables in the current database"

## Example Prompts for Claude

- "List all available MySQL servers"
- "Connect to local-mysql and use the employees database"
- "Run a query to show the first 10 employees"
- "What's the structure of the employees table?"
- "Disconnect from the database"

## Troubleshooting

### Server not appearing in Claude
1. Check the path in the configuration is correct
2. Ensure Python is in your PATH
3. Check Claude Desktop logs for errors

### Connection failures
1. Verify MySQL credentials in servers.json
2. Ensure MySQL server is running
3. Check firewall settings

### Permission errors
1. Ensure the MySQL user has necessary permissions
2. Check file permissions on the MCP server files

## Security Best Practices

1. Use encrypted passwords in production
2. Limit MySQL user permissions
3. Use read-only accounts where possible
4. Keep the config file secure
5. Regularly update dependencies

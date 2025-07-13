#!/usr/bin/env python3
"""
Secure MySQL MCP Password Encryption Tool

This tool helps you encrypt passwords for use in the Secure MySQL MCP configuration.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet

def print_usage():
    """Print usage information"""
    print("""
Secure MySQL MCP Password Encryption Tool

Usage:
    python encrypt_password.py [config_file]

Examples:
    python encrypt_password.py                    # Uses default config/servers.json
    python encrypt_password.py config/prod.json  # Uses specific config file

This tool will:
1. Generate a new encryption key if none exists
2. Encrypt plaintext passwords in the configuration
3. Update the configuration file with encrypted passwords
4. Show you how to use the encrypted passwords
""")

def generate_encryption_key():
    """Generate a new Fernet encryption key"""
    return Fernet.generate_key()

def encrypt_password(password: str, key: bytes) -> str:
    """Encrypt a password using Fernet"""
    cipher = Fernet(key)
    return cipher.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str, key: bytes) -> str:
    """Decrypt a password using Fernet"""
    cipher = Fernet(key)
    return cipher.decrypt(encrypted_password.encode()).decode()

def is_password_encrypted(password: str) -> bool:
    """
    Detect if a password is already encrypted by Fernet
    
    Fernet encrypted strings have these characteristics:
    - Usually 80+ characters long
    - Start with 'gAAAAA' (base64 encoded timestamp)
    - Are valid base64 strings
    """
    if not password or len(password) < 80:
        return False
    
    # Check if it starts with typical Fernet prefix
    if password.startswith('gAAAAA'):
        return True
    
    # Try to decode as base64 and check length
    try:
        import base64
        decoded = base64.urlsafe_b64decode(password.encode())
        # Fernet tokens are at least 57 bytes
        return len(decoded) >= 57
    except:
        return False

def validate_password_safety(password: str, encrypted_flag: bool) -> tuple[bool, str]:
    """
    Validate if it's safe to encrypt this password
    Returns (is_safe, reason)
    """
    is_encrypted = is_password_encrypted(password)
    
    if is_encrypted and not encrypted_flag:
        return False, "Password appears to be encrypted but 'encrypted' flag is False"
    elif is_encrypted and encrypted_flag:
        return False, "Password is already encrypted"
    elif not is_encrypted and encrypted_flag:
        return False, "Password appears to be plaintext but 'encrypted' flag is True"
    else:
        return True, "Safe to encrypt"

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help', 'help']:
        print_usage()
        return

    # Get config file path
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/servers.json"
    config_path = Path(config_path)
    
    # If only filename is provided, default to config directory
    if not config_path.parent.name:
        config_path = Path("config") / config_path
    
    print(f"Using configuration file: {config_path}")
    
    # Check if config file exists
    if not config_path.exists():
        print(f"❌ Configuration file not found: {config_path}")
        print("Please create the configuration file first or run the main server to generate a sample.")
        return
    
    try:
        # Load existing config
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Check if encryption key exists
        if 'encryption_key' not in config:
            print("🔑 No encryption key found in config. Generating new key...")
            key = generate_encryption_key()
            config['encryption_key'] = key.decode()
            print(f"✅ Generated new encryption key: {key.decode()}")
        else:
            key = config['encryption_key'].encode()
            print(f"🔑 Using existing encryption key from config")
        
        # Process each server
        updated_count = 0
        warnings_count = 0
        
        for server in config.get('servers', []):
            server_id = server.get('id', 'unknown')
            
            # Get password
            password = server.get('password')
            if not password:
                print(f"⚠️  Server '{server_id}' has no password, skipping")
                continue
            
            # Validate password safety
            encrypted_flag = server.get('encrypted', False)
            is_safe, reason = validate_password_safety(password, encrypted_flag)
            
            if not is_safe:
                print(f"⚠️  Server '{server_id}': {reason}")
                warnings_count += 1
                
                # For mismatched flags, offer to fix
                if "flag" in reason.lower():
                    print(f"   Do you want to fix the encrypted flag? Current: {encrypted_flag}")
                    response = input("   Fix encrypted flag? (y/N): ").strip().lower()
                    if response in ['y', 'yes']:
                        # Auto-detect correct flag
                        server['encrypted'] = is_password_encrypted(password)
                        print(f"   ✅ Fixed encrypted flag to: {server['encrypted']}")
                        updated_count += 1
                continue
            
            # Skip if already encrypted (double check)
            if encrypted_flag:
                print(f"⏭️  Server '{server_id}' password already encrypted, skipping")
                continue
            
            # Ask user if they want to encrypt this password
            print(f"\n🔒 Server: {server_id} ({server.get('alias', 'No alias')})")
            print(f"   Host: {server.get('host')}:{server.get('port', 3306)}")
            print(f"   User: {server.get('user')}")
            
            # Show password preview (first 10 chars + ...)
            password_preview = password[:10] + "..." if len(password) > 10 else password
            print(f"   Current password: {password_preview}")
            
            response = input("   Encrypt this password? (y/N): ").strip().lower()
            if response in ['y', 'yes']:
                # Double check it's not encrypted
                if is_password_encrypted(password):
                    print(f"   ❌ Error: Password appears to be already encrypted!")
                    warnings_count += 1
                    continue
                
                # Encrypt the password
                encrypted_password = encrypt_password(password, key)
                server['password'] = encrypted_password
                server['encrypted'] = True
                updated_count += 1
                print(f"   ✅ Password encrypted")
            else:
                print(f"   ⏭️  Skipped")
        
        # Show summary
        if warnings_count > 0:
            print(f"\n⚠️  Found {warnings_count} warning(s) - please review the issues above")
        
        if updated_count > 0:
            # Create backup with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = config_path.with_suffix(f'.backup.{timestamp}')
            if config_path.exists():
                config_path.rename(backup_path)
                print(f"\n📋 Created backup at: {backup_path}")
            
            # Save updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            print(f"✅ Updated configuration saved to: {config_path}")
            if warnings_count == 0:
                print(f"🔐 Encrypted passwords for {updated_count} server(s)")
            else:
                print(f"🔧 Updated {updated_count} configuration(s)")
            
            # Show security recommendations
            print("\n🛡️  Security Recommendations:")
            print("   1. Keep the encryption key secure")
            print("   2. Don't share the configuration file")
            print("   3. Use file permissions to protect config files (chmod 600)")
            print("   4. Consider using environment variables for sensitive data")
            print("   5. Regularly rotate encryption keys")
            print("   6. Re-run this tool to verify no issues remain")
            
            # Show how to verify
            print(f"\n🧪 To verify the configuration, you can test with:")
            print(f"   python test_client.py")
            print(f"   > list")
            
            # Important backup file reminder
            print(f"\n🚨 重要安全提醒 / SECURITY WARNING:")
            print(f"   ⚠️  备份文件已创建: {backup_path}")
            print(f"   ⚠️  Backup file created: {backup_path}")
            print(f"   🔥 请立即处理备份文件 (包含明文密码!):")
            print(f"   🔥 Handle backup file immediately (contains plaintext passwords!):")
            print(f"   • 方案1: 安全保存备份 / Option 1: Securely store backup")
            print(f"   • 方案2: 立即删除备份 / Option 2: Delete backup now:")
            print(f"     rm {backup_path}")
            print(f"   ❗ 不处理备份文件会导致安全风险!")
            print(f"   ❗ Leaving backup unhandled creates security risk!")
            
        elif warnings_count == 0:
            print("\n💡 No passwords needed encryption")
            print("   All passwords are already properly encrypted or you chose to skip them")
        else:
            print("\n💡 No changes made")
            print("   Please review the warnings above and re-run if needed")
        
    except Exception as e:
        print(f"❌ Error processing configuration: {e}")
        return

if __name__ == "__main__":
    main() 
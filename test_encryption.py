#!/usr/bin/env python3
"""
Test script to verify password encryption safety
"""

import json
import tempfile
import os
from pathlib import Path
from encrypt_password import is_password_encrypted, validate_password_safety, encrypt_password, generate_encryption_key

def test_encryption_detection():
    """Test if we can correctly detect encrypted passwords"""
    print("🧪 Testing password encryption detection...")
    
    # Generate test key
    key = generate_encryption_key()
    
    # Test cases
    test_cases = [
        ("plaintext_password", False),
        ("short", False),
        ("another_plain_password_123", False),
        (encrypt_password("test_password", key), True),
        (encrypt_password("another_test", key), True),
    ]
    
    all_passed = True
    for password, expected_encrypted in test_cases:
        result = is_password_encrypted(password)
        status = "✅" if result == expected_encrypted else "❌"
        print(f"  {status} Password: '{password[:20]}...' - Expected: {expected_encrypted}, Got: {result}")
        if result != expected_encrypted:
            all_passed = False
    
    return all_passed

def test_validation_safety():
    """Test password validation safety"""
    print("\n🧪 Testing password validation safety...")
    
    key = generate_encryption_key()
    plaintext = "my_password"
    encrypted = encrypt_password(plaintext, key)
    
    test_cases = [
        (plaintext, False, True, "Plaintext with correct flag"),
        (encrypted, True, False, "Encrypted with correct flag"),
        (plaintext, True, False, "Plaintext with wrong flag"),
        (encrypted, False, False, "Encrypted with wrong flag"),
    ]
    
    all_passed = True
    for password, encrypted_flag, expected_safe, description in test_cases:
        is_safe, reason = validate_password_safety(password, encrypted_flag)
        status = "✅" if is_safe == expected_safe else "❌"
        print(f"  {status} {description} - Expected safe: {expected_safe}, Got: {is_safe}")
        if is_safe != expected_safe:
            print(f"      Reason: {reason}")
            all_passed = False
    
    return all_passed

def test_double_encryption_prevention():
    """Test that double encryption is prevented"""
    print("\n🧪 Testing double encryption prevention...")
    
    # Create a temporary config file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        test_config = {
            "encryption_key": generate_encryption_key().decode(),
            "servers": [
                {
                    "id": "test-server",
                    "alias": "Test Server",
                    "host": "localhost",
                    "port": 3306,
                    "user": "test",
                    "password": "plain_password",
                    "encrypted": False
                }
            ]
        }
        json.dump(test_config, f, indent=2)
        config_path = f.name
    
    try:
        # First encryption should work
        print(f"  Testing first encryption...")
        
        # Load and check initial state
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        server = config['servers'][0]
        original_password = server['password']
        
        # Simulate first encryption
        key = config['encryption_key'].encode()
        encrypted_password = encrypt_password(original_password, key)
        server['password'] = encrypted_password
        server['encrypted'] = True
        
        # Save the encrypted config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"    ✅ First encryption completed")
        
        # Now test detection of already encrypted password
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        server = config['servers'][0]
        is_safe, reason = validate_password_safety(server['password'], server['encrypted'])
        
        if not is_safe and "already encrypted" in reason:
            print(f"    ✅ Double encryption correctly prevented: {reason}")
            return True
        else:
            print(f"    ❌ Double encryption NOT prevented: {reason}")
            return False
            
    finally:
        # Clean up
        os.unlink(config_path)

def main():
    """Run all tests"""
    print("🔒 Testing Password Encryption Safety\n")
    
    tests = [
        ("Encryption Detection", test_encryption_detection),
        ("Validation Safety", test_validation_safety),
        ("Double Encryption Prevention", test_double_encryption_prevention),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        if test_func():
            print(f"✅ {test_name} PASSED\n")
            passed += 1
        else:
            print(f"❌ {test_name} FAILED\n")
    
    print(f"📊 Test Results: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All tests passed! The encryption tool is safe from double encryption.")
    else:
        print("⚠️ Some tests failed. Please review the encryption logic.")

if __name__ == "__main__":
    main() 
# Azure Speech API Key Tester
# Quick script to test if your Azure Speech keys are valid

import requests
import os
from dotenv import load_dotenv

load_dotenv()

def test_speech_key(key, endpoint, region):
    """Test if an Azure Speech API key is valid"""
    print(f"\n🔍 Testing key: {key[:10]}...{key[-10:]}")
    print(f"   Endpoint: {endpoint}")
    print(f"   Region: {region}")
    
    # Method 1: Try to create a test transcription (this will fail but show if auth works)
    url = f"{endpoint}/speechtotext/v3.2/transcriptions"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json"
    }
    
    # Try a simple GET request first (list transcriptions)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"   Response: {response.status_code}")
        
        if response.status_code == 200:
            print("   ✅ Key is VALID and working!")
            return True
        elif response.status_code == 401:
            print("   ❌ Key is INVALID or EXPIRED")
            print(f"   Error: {response.text}")
            return False
        else:
            print(f"   ⚠️  Unexpected response: {response.status_code}")
            print(f"   Details: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Connection error: {e}")
        return False

print("=" * 70)
print("Azure Speech API Key Validator")
print("=" * 70)

# Test Primary Key
print("\n📌 PRIMARY KEY TEST:")
primary_key = os.getenv("AZURE_SPEECH_KEY", "")
primary_endpoint = os.getenv("AZURE_SPEECH_KEY_ENDPOINT", "https://westus.api.cognitive.microsoft.com/")
primary_region = os.getenv("AZURE_REGION", "westus")

if primary_key and primary_key != "dummy_primary_will_use_backup":
    primary_valid = test_speech_key(primary_key, primary_endpoint, primary_region)
else:
    print("   ⏭️  Skipping (dummy or not set)")
    primary_valid = False

# Test Backup Key  
print("\n📌 BACKUP KEY TEST:")
backup_key = os.getenv("AZURE_SPEECH_KEY_BACKUP", "")
backup_endpoint = os.getenv("AZURE_SPEECH_KEY_ENDPOINT_BACKUP", "https://eastus.api.cognitive.microsoft.com/")
backup_region = os.getenv("AZURE_REGION_BACKUP", "eastus")

if backup_key:
    backup_valid = test_speech_key(backup_key, backup_endpoint, backup_region)
else:
    print("   ❌ Backup key not found in .env file")
    backup_valid = False

# Summary
print("\n" + "=" * 70)
print("📊 SUMMARY:")
print("=" * 70)
print(f"Primary Key:  {'✅ VALID' if primary_valid else '❌ INVALID'}")
print(f"Backup Key:   {'✅ VALID' if backup_valid else '❌ INVALID'}")

if not primary_valid and not backup_valid:
    print("\n🚨 CRITICAL: Both keys are invalid!")
    print("\n💡 TO FIX THIS ISSUE:")
    print("   1. Go to Azure Portal: https://portal.azure.com")
    print("   2. Navigate to your Speech Service resource")
    print("   3. Click 'Keys and Endpoint' in the left menu")
    print("   4. Copy KEY 1 or KEY 2")
    print("   5. Update the keys in your .env file:")
    print("      AZURE_SPEECH_KEY_BACKUP=<your-new-key>")
    print("   6. Restart the Docker container:")
    print("      docker-compose down && docker-compose up -d")
elif not backup_valid:
    print("\n⚠️  WARNING: Backup key is invalid, but primary is working")
else:
    print("\n✅ At least one key is working!")

print("=" * 70)

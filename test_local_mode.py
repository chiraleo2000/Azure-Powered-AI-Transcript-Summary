"""
Quick test script for LOCAL_TESTING_MODE
Tests all mock services are working
"""
import os
os.environ['LOCAL_TESTING_MODE'] = 'True'

from dotenv import load_dotenv
load_dotenv()

print("=" * 80)
print("🧪 TESTING LOCAL MOCK SERVICES")
print("=" * 80)

# Test 1: Mock Storage
print("\n1️⃣ Testing Mock Storage...")
from local_mock import get_mock_storage
storage = get_mock_storage()
storage.upload_blob("transcripts", "test.txt", "Hello World!")
data = storage.download_blob("transcripts", "test.txt")
assert data == "Hello World!", "Storage test failed!"
print("✅ Mock Storage working")

# Test 2: Mock Transcription
print("\n2️⃣ Testing Mock Transcription...")
from local_mock import get_mock_transcription
transcription = get_mock_transcription()
text, success = transcription.transcribe_audio("dummy.mp3", "th-TH")
assert success, "Transcription test failed!"
assert len(text) > 0, "Transcription returned no text!"
print(f"✅ Mock Transcription working ({len(text)} chars)")

# Test 3: Mock AI
print("\n3️⃣ Testing Mock AI...")
from local_mock import get_mock_ai
ai = get_mock_ai()
summary = ai.summarize("Test content for summarization", "comprehensive", "")
assert len(summary) > 0, "AI summary test failed!"
print(f"✅ Mock AI working ({len(summary)} chars)")

# Test 4: Mock OCR
print("\n4️⃣ Testing Mock OCR...")
from local_mock import get_mock_ocr
ocr = get_mock_ocr()
text = ocr.extract_text_from_image("dummy.jpg")
assert len(text) > 0, "OCR test failed!"
print(f"✅ Mock OCR working ({len(text)} chars)")

# Test 5: Backend imports
print("\n5️⃣ Testing Backend with LOCAL_TESTING_MODE...")
try:
    from backend import LOCAL_TESTING_MODE, transcription_manager
    assert LOCAL_TESTING_MODE == True, "LOCAL_TESTING_MODE not enabled in backend!"
    print("✅ Backend loaded with LOCAL_TESTING_MODE")
except Exception as e:
    print(f"❌ Backend loading failed: {e}")
    raise

# Test 6: AI Summary imports  
print("\n6️⃣ Testing AI Summary with LOCAL_TESTING_MODE...")
try:
    from ai_summary import LOCAL_TESTING_MODE as AI_LOCAL_MODE, ai_summary_manager
    assert AI_LOCAL_MODE == True, "LOCAL_TESTING_MODE not enabled in ai_summary!"
    print("✅ AI Summary loaded with LOCAL_TESTING_MODE")
except Exception as e:
    print(f"❌ AI Summary loading failed: {e}")
    raise

print("\n" + "=" * 80)
print("✅ ALL TESTS PASSED - LOCAL MODE IS READY!")
print("=" * 80)
print("\n📋 Next steps:")
print("1. Make sure .env has: LOCAL_TESTING_MODE=True")
print("2. Run: python app.py")
print("3. Open: http://localhost:7860")
print("4. Register a user and test transcription/summary")
print("\n💡 All Azure services are mocked - no API calls will be made!")
print("💾 Data stored in: ./local_storage/")

import os, json, time
from datetime import datetime
from typing import List, Optional, Tuple

class LocalMockStorage:
    def __init__(self):
        self.base_dir = 'local_storage'
        os.makedirs(self.base_dir, exist_ok=True)
        for c in ['transcripts', 'response-chats', 'user-password', 'meta-storage']:
            os.makedirs(f'{self.base_dir}/{c}', exist_ok=True)
    def upload_blob(self, container, blob_name, data):
        path = f'{self.base_dir}/{container}/{blob_name}'
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w', encoding='utf-8').write(data)
        return f'file://{path}'
    def download_blob(self, container, blob_name):
        path = f'{self.base_dir}/{container}/{blob_name}'
        return open(path, 'r', encoding='utf-8').read() if os.path.exists(path) else None
    def list_blobs(self, container, prefix=''):
        path = f'{self.base_dir}/{container}'
        return [f.replace('\\','/') for root, dirs, files in os.walk(path) for f in [os.path.relpath(os.path.join(root,x),path) for x in files] if not prefix or f.startswith(prefix)] if os.path.exists(path) else []
    def delete_blob(self, container, blob_name):
        path = f'{self.base_dir}/{container}/{blob_name}'
        if os.path.exists(path): os.remove(path); return True
        return False
    def blob_exists(self, container, blob_name):
        return os.path.exists(f'{self.base_dir}/{container}/{blob_name}')

class LocalMockTranscription:
    def transcribe_audio(self, path, lang='th-TH'):
        print(f'[MOCK] Transcribing: {os.path.basename(path)}')
        time.sleep(1)
        return f'Mock transcript - {datetime.now()}\nTest meeting content.', True

class LocalMockAI:
    def summarize(self, content, stype='comprehensive', prompt=''):
        print(f'[MOCK] Summarizing...')
        time.sleep(1)
        return f'# Mock Summary\n\nType: {stype}\nWords:{len(content.split())}\n\nLocal Testing Mode Active ✅'

class LocalMockOCR:
    def extract_text_from_image(self, path):
        print(f'[MOCK] OCR: {os.path.basename(path)}')
        return f'Mock OCR text from {os.path.basename(path)}'

_s, _t, _a, _o = None, None, None, None
def get_mock_storage():
    global _s
    if not _s: _s = LocalMockStorage()
    return _s
def get_mock_transcription():
    global _t
    if not _t: _t = LocalMockTranscription()
    return _t
def get_mock_ai():
    global _a
    if not _a: _a = LocalMockAI()
    return _a
def get_mock_ocr():
    global _o
    if not _o: _o = LocalMockOCR()
    return _o

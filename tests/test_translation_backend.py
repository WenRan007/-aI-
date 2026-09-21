import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import translator_desktop as desktop


class TranslationBackendTests(unittest.TestCase):
    def backend(self, root):
        with patch.object(desktop, 'APP_DIR', Path(root)), patch.dict('os.environ', {}, clear=True):
            return desktop.TranslationBackend()

    def test_detected_chinese_does_not_require_api_key(self):
        with tempfile.TemporaryDirectory() as root:
            b = self.backend(root)
            segments = [desktop.Segment(0, 1, '今天买一送三')]
            with patch.object(b, '_key', side_effect=AssertionError('key must not be read')):
                result = b.translate(segments, 'auto', 'zh-CN', True, True, 'zh')
            self.assertEqual(result[0].translated, '今天买一送三')

    def test_local_engine_does_not_call_deepl(self):
        with tempfile.TemporaryDirectory() as root:
            b = self.backend(root)
            b.engine = 'ollama'
            with patch.object(b._ollama, 'translate', return_value=['价格20美元']) as local:
                with patch.object(b, '_key', side_effect=AssertionError('no external key')):
                    result = b.translate([desktop.Segment(0, 1, 'Twenty dollars')], 'auto', 'zh-CN', True, True, 'en')
            local.assert_called_once_with(['Twenty dollars'], 'EN')
            self.assertEqual(result[0].translated, '价格20美元')

    def test_quota_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as root:
            b = self.backend(root)
            with patch.object(b, '_key', return_value='test-key:fx'), patch('urllib.request.urlopen',
                    side_effect=HTTPError('https://example.invalid', 456, 'quota', None, None)) as request:
                with self.assertRaisesRegex(RuntimeError, '额度已用尽'):
                    b.translate([desktop.Segment(0, 1, 'Hello world')], 'en', 'zh-CN', True, True, 'en')
                self.assertEqual(request.call_count, 1)

    def test_failed_asr_cannot_generate_placeholder_text(self):
        with tempfile.TemporaryDirectory() as root:
            b = self.backend(root)
            work = Path(root) / 'work'
            work.mkdir()
            class Info:
                language = 'en'
            class EmptyModel:
                def transcribe(self, *args, **kwargs):
                    return iter([]), Info()
            b._whisper = EmptyModel()
            with patch.object(b, '_extract_audio', return_value=(str(work), 'test.wav')):
                with self.assertRaisesRegex(RuntimeError, '未识别到有效口播'):
                    b.transcribe_with_language('test.mp4', 'auto')


if __name__ == '__main__':
    unittest.main()

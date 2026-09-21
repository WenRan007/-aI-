import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_translation import OllamaTranslator
from translation_runtime import load_config


class LocalTranslationTests(unittest.TestCase):
    def test_ids_reassemble_order(self):
        result = OllamaTranslator.parse_result(json.dumps({'translations': [
            {'id': 2, 'text': '价格是20美元。'}, {'id': 1, 'text': '欢迎来到常客。'}]}), 2)
        self.assertEqual(result, ['欢迎来到常客。', '价格是20美元。'])

    def test_invalid_output_rejected(self):
        for data in ['not json', '{}', '{"translations":[]}',
                     '{"translations":[{"id":1,"text":""}]}',
                     '{"translations":[{"id":true,"text":"你好"}]}',
                     '{"translations":[{"id":2,"text":"你好"}]}']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                OllamaTranslator.parse_result(data, 1)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            OllamaTranslator.parse_result(json.dumps({'translations': [
                {'id': 1, 'text': '你好'}, {'id': 1, 'text': '你好'}]}), 2)

    def test_missing_rows_never_copy_source(self):
        tr = OllamaTranslator()
        tr._ready = True
        with patch.object(tr, '_request', return_value={'message': {'content': '{}'}}) as request:
            with self.assertRaisesRegex(RuntimeError, '译文校验失败'):
                tr.translate(['This is a source sentence.'])
            self.assertEqual(request.call_count, 2)

    def test_untranslated_output_is_not_success(self):
        tr = OllamaTranslator()
        tr._ready = True
        value = {'message': {'content': json.dumps({'translations': [
            {'id': 1, 'text': 'This is a source sentence.'}]})}}
        with patch.object(tr, '_request', return_value=value):
            with self.assertRaisesRegex(RuntimeError, '中文译文'):
                tr.translate(['This is a source sentence.'])

    def test_uninstalled_model_fails_clearly(self):
        tr = OllamaTranslator()
        with patch.object(tr, '_request', return_value={'models': []}):
            with self.assertRaisesRegex(RuntimeError, '未安装'):
                tr.ensure_ready()

    def test_remote_service_does_not_start_local_process(self):
        tr = OllamaTranslator(host='http://example.com:11434')
        with patch.object(tr, '_request', side_effect=OSError()), patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '仅允许'):
                tr.ensure_ready()
            launch.assert_not_called()

    def test_relative_config_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'models' / 'turbo').mkdir(parents=True)
            (root / 'translation_config.json').write_text(json.dumps({
                'whisper_model': 'models/turbo', 'ollama_executable': 'runtime/ollama.exe'}))
            with patch.dict('os.environ', {}, clear=True):
                config = load_config(root)
            self.assertEqual(Path(config['whisper_model']), root / 'models' / 'turbo')
            self.assertEqual(Path(config['ollama_executable']), root / 'runtime' / 'ollama.exe')


if __name__ == '__main__':
    unittest.main()

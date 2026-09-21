"""Exercise the real desktop queue, ASR, automatic local service and Chinese output."""
import json
import re
import time
import traceback
from pathlib import Path


def run(module, folder):
    folder = Path(folder)
    report = {'passed': False, 'version': '2.0.3', 'recognition': [], 'translation': []}
    app = None
    try:
        videos = json.loads((folder / 'inputs.json').read_text(encoding='utf-8'))['videos']
        app = module.TranslatorApp()
        app.withdraw()
        report['configured_model'] = app.backend._model_name
        report['engine'] = app.backend.engine
        asr, translate = app.backend.transcribe_with_language, app.backend.translate

        def recognize(video, language):
            start = time.perf_counter()
            result, detected = asr(video, language)
            report['recognition'].append({'video': video, 'seconds': round(time.perf_counter() - start, 2),
                'language': detected, 'device': app.backend.asr_status,
                'segments': [{'start': s.start, 'end': s.end, 'text': s.text} for s in result]})
            return result, detected

        def translated(*args):
            start = time.perf_counter()
            result = translate(*args)
            report['translation'].append({'seconds': round(time.perf_counter() - start, 2),
                                          'text': [s.translated for s in result]})
            return result

        app.backend.transcribe_with_language = recognize
        app.backend.translate = translated
        app.jobs = [module.Job(str(video), 'auto', 'zh-CN', '批量翻译短视频') for video in videos]
        app.refresh()
        start = time.perf_counter()
        app.start_jobs()
        deadline = time.monotonic() + 900
        while app.worker.is_alive():
            app.update()
            time.sleep(.02)
            if time.monotonic() > deadline:
                raise RuntimeError('batch test timed out')
        # Drain completion events through the same UI handler.
        for _ in range(10):
            app.update()
            time.sleep(.03)
        report['seconds'] = round(time.perf_counter() - start, 2)
        report['jobs'] = [job.__dict__.copy() for job in app.jobs]
        assert all(j.status == '已完成' and j.progress == 100 for j in app.jobs), report['jobs']
        assert all(re.search(r'[\u3400-\u9fff]', j.translation_text) for j in app.jobs)
        assert app.provider_combo['state'] == 'readonly' or str(app.provider_combo['state']) == 'readonly'
        report['passed'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if app is not None:
            app.destroy()
        (folder / 'translation_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1

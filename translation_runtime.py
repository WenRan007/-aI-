"""Portable, non-secret translation configuration and Windows CUDA discovery."""
import json
import os
from pathlib import Path

_dll_handles = []

def load_config(app_dir):
    path = Path(app_dir) / 'translation_config.json'
    config = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    mapping = {'WHISPER_MODEL': 'whisper_model', 'WHISPER_DEVICE': 'whisper_device',
               'WHISPER_COMPUTE_TYPE': 'whisper_compute_type', 'TRANSLATION_ENGINE': 'translation_engine',
               'OLLAMA_HOST': 'ollama_host', 'OLLAMA_MODEL': 'ollama_model'}
    for env, key in mapping.items():
        if os.getenv(env):
            config[key] = os.environ[env]
    for key in ('ollama_executable', 'ollama_models_dir', 'cuda_dir'):
        if config.get(key):
            p = Path(config[key])
            config[key] = str(p if p.is_absolute() else Path(app_dir) / p)
    model = config.get('whisper_model')
    if model and (Path(app_dir) / model).is_dir():
        config['whisper_model'] = str((Path(app_dir) / model).resolve())
    if not model:
        snapshots = list((Path(app_dir) / 'models_cache' / 'hub' / 'models--Systran--faster-whisper-small' / 'snapshots').glob('*/model.bin'))
        config['whisper_model'] = str(snapshots[0].parent) if snapshots else 'small'
    return config

def setup_cuda(config, app_dir):
    directories = [config.get('cuda_dir'), str(Path(app_dir) / 'cuda')]
    found = []
    for item in directories:
        if item and Path(item).is_dir():
            directory = str(Path(item).resolve())
            if directory not in found:
                found.append(directory)
                if hasattr(os, 'add_dll_directory'):
                    _dll_handles.append(os.add_dll_directory(directory))
    if found:
        os.environ['PATH'] = os.pathsep.join(found + [os.environ.get('PATH', '')])

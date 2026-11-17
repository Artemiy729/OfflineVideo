"""Register the helper for installed Chromium browsers on macOS."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = {
    'Dia': ['Dia'],
    'Arc': ['Arc/User Data', 'Google/Chrome'],
    'Google Chrome': ['Google/Chrome'],
    'Google Chrome Canary': ['Google/Chrome Canary'],
    'Microsoft Edge': ['Microsoft Edge'],
    'Brave Browser': ['BraveSoftware/Brave-Browser'],
    'Chromium': ['Chromium'],
    'Vivaldi': ['Vivaldi'],
    'Yandex': ['Yandex/YandexBrowser'],
    'Opera': ['com.operasoftware.Opera'],
}


def copy_environment(source, destination):
    def ignore(directory, names):
        ignored = {'__pycache__'}
        for name in names:
            original = Path(directory) / name
            target = destination / original.relative_to(source)
            if original.is_symlink() and target.is_symlink():
                if os.readlink(original) == os.readlink(target):
                    ignored.add(name)
                else:
                    target.unlink()
        return ignored
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True, ignore=ignore)


def extension_id():
    manifest = json.loads((ROOT / 'extension/manifest.json').read_text())
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(char, 16)) for char in digest)


def install():
    if sys.platform != 'darwin':
        raise SystemExit('This installer supports macOS only.')
    # Browsers may have no macOS permission to execute files on Desktop.
    runtime = Path.home() / 'Library/Application Support/OfflineVideo/runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    (runtime / 'native').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'native/host.py', runtime / 'native/host.py')
    copy_environment(ROOT / '.venv', runtime / '.venv')
    subprocess.run([str(runtime / '.venv/bin/python'), str(runtime / 'native/host.py'), '--purge-history'], check=True)
    launcher = runtime / 'host.command'
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(runtime / '.venv/bin/python')) + ' ' + shlex.quote(str(runtime / 'native/host.py')) + ' "$@"\n')
    launcher.chmod(0o755)
    manifest = {'name': 'local.offlinevideo', 'description': 'OfflineVideo local download helper',
                'path': str(launcher), 'type': 'stdio',
                'allowed_origins': [f'chrome-extension://{extension_id()}/']}
    support = Path.home() / 'Library/Application Support'
    installed = []
    for browser, directories in BROWSERS.items():
        if not (Path('/Applications') / f'{browser}.app').exists() and not any((support / directory).exists() for directory in directories[:1]):
            continue
        for directory in directories:
            target = support / directory / 'NativeMessagingHosts'
            target.mkdir(parents=True, exist_ok=True)
            (target / 'local.offlinevideo.json').write_text(json.dumps(manifest, indent=2) + '\n')
        installed.append(browser)
    if not installed:
        raise SystemExit('No supported browser found. Install and open Dia or Chrome first.')
    print('Helper registered for: ' + ', '.join(installed))
    print('Extension ID: ' + extension_id())
    print('Restart your browser. Open chrome://extensions, enable Developer mode,')
    print('click Load unpacked, and select:\n' + str(ROOT / 'extension'))


if __name__ == '__main__':
    install()

import functools
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('host', ROOT / 'native/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class ProtocolTests(unittest.TestCase):

    def test_updating_installed_environment_preserves_existing_symlinks(self):
        spec = importlib.util.spec_from_file_location('installer', ROOT / 'native/install.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'source', Path(folder) / 'destination'
            source.mkdir()
            (source / 'python3').write_text('python')
            (source / 'python').symlink_to('python3')
            installer.copy_environment(source, destination)
            (source / 'package.txt').write_text('updated')
            installer.copy_environment(source, destination)
            self.assertTrue((destination / 'python').is_symlink())
            self.assertEqual((destination / 'package.txt').read_text(), 'updated')

    def test_completed_records_are_removed_without_touching_videos(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, 'STATE', Path(folder)):
            job_id = str(uuid.uuid4())
            video = Path(folder) / 'lesson.mp4'
            video.write_bytes(b'keep')
            host.save({'id': job_id, 'status': 'done', 'files': [str(video)]})
            host.job_path(job_id).with_suffix('.log').write_text('old log')
            host.purge_history(all_finished=True)
            self.assertFalse(host.job_path(job_id).exists())
            self.assertFalse(host.job_path(job_id).with_suffix('.log').exists())
            self.assertEqual(video.read_bytes(), b'keep')
            self.assertNotIn('jobs', host.dispatch({'action': 'status'}))

    def test_publication_never_overwrites_an_existing_video(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'lesson.mp4'
            source.write_bytes(b'new')
            destination = Path(folder) / 'chosen'
            destination.mkdir()
            (destination / 'lesson.mp4').write_bytes(b'original')
            target = host.publish_mp4(source, destination)
            self.assertEqual(target.name, 'lesson (1).mp4')
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual((destination / 'lesson.mp4').read_bytes(), b'original')
            self.assertEqual(len(list(destination.iterdir())), 2)

    def test_roundtrip_utf8(self):
        stream = io.BytesIO()
        host.send(stream, {'title': 'Лекция №1'})
        stream.seek(0)
        self.assertEqual(host.receive(stream), {'title': 'Лекция №1'})

    def test_rejects_oversize_truncated_and_non_object(self):
        for message in [struct.pack('=I', 65537), b'\x04', struct.pack('=I', 5) + b'{}', struct.pack('=I', 2) + b'[]']:
            with self.subTest(message=message), self.assertRaises((ValueError, EOFError)):
                host.receive(io.BytesIO(message))

    def test_rejects_unsafe_urls_and_job_paths(self):
        for url in ['file:///etc/passwd', '--exec=whoami', 'https://name:secret@example.com', 'javascript:alert(1)', 'https://']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                host.validate_url(url)
        with self.assertRaises(ValueError):
            host.job_path('../../secret')

    def test_worker_rejects_playlist_before_download(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, 'STATE', Path(folder)), patch.object(host, 'DOWNLOADS', Path(folder) / 'output'):
            job_id = str(uuid.uuid4())
            host.save({'id': job_id, 'url': 'https://example.com/list', 'quality': '720', 'title': 'List'})
            from unittest.mock import MagicMock
            downloader = MagicMock()
            downloader.__enter__.return_value = downloader
            downloader.extract_info.return_value = {'_type': 'playlist', 'entries': []}
            with patch('yt_dlp.YoutubeDL', return_value=downloader):
                host.worker(job_id)
            self.assertEqual(host.read_job(job_id)['status'], 'error')
            downloader.process_ie_result.assert_not_called()


    def test_picker_persists_selection_and_cancel_keeps_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, 'STATE', Path(folder) / 'state'):
            selected = Path(folder) / 'Folder with spaces'
            selected.mkdir()
            with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, str(selected) + '\n', '')):
                host.choose_directory()
            self.assertEqual(host.download_directory(), selected.resolve())
            with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'User canceled. (-128)')):
                host.choose_directory()
            self.assertEqual(host.download_directory(), selected.resolve())
            self.assertEqual(host.read_json(host.STATE / 'picker.json', {})['state'], 'cancelled')


class DownloadTests(unittest.TestCase):
    def test_webm_becomes_h264_aac_mp4_and_source_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'lesson.webm'
            ffmpeg = host.executable('ffmpeg')
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            'color=c=green:s=160x90:d=1', '-f', 'lavfi', '-i', 'sine=duration=1',
                            '-c:v', 'libvpx-vp9', '-c:a', 'libopus', '-shortest', str(source)], check=True)
            original = source.read_bytes()
            target = host.compatible_mp4(source)
            self.assertEqual(target.suffix, '.mp4')
            self.assertEqual(source.read_bytes(), original)
            result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(target)], capture_output=True, text=True)
            self.assertIn('Video: h264', result.stderr)
            self.assertIn('Audio: aac', result.stderr)
            self.assertIn('yuv420p', result.stderr)
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(target), '-f', 'null', '-'], check=True)


    def test_native_host_starts_detached_worker_and_downloads_video(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            media = base / 'fixture.webm'
            subprocess.run([host.executable('ffmpeg'), '-hide_banner', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'color=c=green:s=160x90:d=1', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=1', '-c:v', 'libvpx-vp9', '-c:a', 'libopus', '-shortest', str(media)], check=True)
            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_args):
                    pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=folder))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            env = {**os.environ, 'OFFLINEVIDEO_STATE': str(base / 'state')}
            env.pop('OFFLINEVIDEO_DOWNLOADS', None)
            selected = base / 'my chosen folder'
            selected.mkdir()
            (base / 'state').mkdir()
            (base / 'state/settings.json').write_text(json.dumps({'directory': str(selected)}))
            def call(message):
                stream = io.BytesIO()
                host.send(stream, message)
                result = subprocess.run([sys.executable, str(ROOT / 'native/host.py')], input=stream.getvalue(), capture_output=True, env=env, check=True)
                return host.receive(io.BytesIO(result.stdout))
            try:
                self.assertTrue(call({'action': 'status'})['capabilities']['downloader'])
                job_id = str(uuid.uuid4())
                message = {'action': 'download', 'id': job_id, 'url': f'http://127.0.0.1:{server.server_port}/fixture.webm', 'quality': '1080'}
                response = call(message)
                self.assertTrue(response['ok'], response)
                self.assertEqual(call(message)['id'], job_id)
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    status = call({'action': 'task_status', 'id': job_id})
                    if status['state'] in ('done', 'error'):
                        break
                    time.sleep(.2)
                self.assertEqual(status['state'], 'done', status)
                files = list(selected.iterdir())
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].suffix, '.mp4')
                result = subprocess.run([host.executable('ffmpeg'), '-hide_banner', '-i', str(files[0])], capture_output=True, text=True)
                self.assertIn('Video: h264', result.stderr)
                self.assertIn('Audio: aac', result.stderr)
                subprocess.run([host.executable('ffmpeg'), '-v', 'error', '-i', str(files[0]), '-f', 'null', '-'], check=True)
                self.assertNotIn('jobs', call({'action': 'status'}))
                self.assertFalse((base / 'state/jobs' / f'{job_id}.json').exists())
                self.assertEqual(call({'action': 'task_status', 'id': job_id})['state'], 'expired')
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()

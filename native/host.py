#!/usr/bin/env python3
"""OfflineVideo native messaging host and detached download worker."""
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import tempfile
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('OFFLINEVIDEO_STATE', Path.home() / 'Library/Application Support/OfflineVideo'))
DOWNLOADS = Path(os.environ.get('OFFLINEVIDEO_DOWNLOADS', Path.home() / 'Downloads/OfflineVideo'))
QUALITIES = {'720', '1080', 'best'}


def executable(name):
    found = shutil.which(name, path=os.pathsep.join([str(ROOT / '.venv/bin'), '/opt/homebrew/bin', '/usr/local/bin', os.environ.get('PATH', '')]))
    if not found and name == 'ffmpeg':
        try:
            import imageio_ffmpeg
            found = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            pass
    return found


def validate_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('Invalid URL.')
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Enter an HTTP or HTTPS URL without a username or password.')
    return value


def job_path(job_id):
    if not isinstance(job_id, str) or str(uuid.UUID(job_id)) != job_id:
        raise ValueError('Invalid request ID.')
    return STATE / 'jobs' / f'{job_id}.json'


def save(job):
    path = job_path(job['id'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    job['updated'] = time.time()
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False))
    temporary.replace(path)


def read_job(job_id):
    return json.loads(job_path(job_id).read_text())


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False))
    temp.replace(path)


def download_directory():
    if 'OFFLINEVIDEO_DOWNLOADS' in os.environ:
        return DOWNLOADS
    value = read_json(STATE / 'settings.json', {}).get('directory')
    return Path(value) if value and Path(value).is_absolute() else DOWNLOADS


def spawn(arguments, log_name):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / log_name).open('ab') as log:
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *arguments],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, close_fds=True)


def choose_directory():
    try:
        result = subprocess.run(['/usr/bin/osascript', '-e',
            'POSIX path of (choose folder with prompt "Choose a download folder for OfflineVideo")'],
            capture_output=True, text=True, timeout=300)
        if result.returncode:
            if '-128' in result.stderr:
                write_json(STATE / 'picker.json', {'state': 'cancelled'})
                return
            raise ValueError('Could not open the folder picker. ' + result.stderr.strip())
        directory = Path(result.stdout.strip()).resolve()
        if not directory.is_dir():
            raise ValueError('The selected folder is not available.')
        write_json(STATE / 'settings.json', {'directory': str(directory)})
        write_json(STATE / 'picker.json', {'state': 'done'})
    except Exception as exc:
        write_json(STATE / 'picker.json', {'state': 'error', 'error': str(exc)})


def compatible_mp4(source):
    """Keep the source; publish a complete H.264/AAC MP4 atomically."""
    source = Path(source)
    ffmpeg = executable('ffmpeg')
    probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(source)], capture_output=True, text=True)
    description = probe.stderr
    if 'Video:' not in description:
        raise ValueError('No video stream was found in the downloaded file.')
    video_ok = bool(re.search(r'Video: h264\b', description) and re.search(r'\byuv420p\b', description))
    audio_ok = 'Audio:' not in description or bool(re.search(r'Audio: aac\b', description))
    if source.suffix.lower() == '.mp4' and video_ok and audio_ok:
        return source
    target = source.with_suffix('.mp4')
    # Never overwrite a previously saved MP4 next to an MKV.
    if target != source and target.exists():
        target = source.with_name(source.stem + ' (compatible).mp4')
        if target.exists():
            raise ValueError('A compatible MP4 already exists next to the source file.')
    temporary = target.with_name(target.stem + f'.{uuid.uuid4().hex}.converting.mp4')
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(source),
               '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy' if video_ok else 'libx264']
    if not video_ok:
        command += ['-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
    command += ['-c:a', 'copy' if audio_ok else 'aac']
    if not audio_ok:
        command += ['-b:a', '192k']
    command += ['-movflags', '+faststart', str(temporary)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Could not prepare the MP4: ' + result.stderr[-1000:])
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def purge_history(all_finished=False):
    for path in (STATE / 'jobs').glob('*.json'):
        job = read_json(path, {})
        age = time.time() - job.get('updated', 0)
        discard = job.get('status') in ('done', 'error') and (all_finished or age > 60)
        if job.get('status') in ('queued', 'downloading', 'processing') and age > 180:
            try:
                pid = int(job.get('pid', 0))
                if pid <= 0:
                    raise ProcessLookupError
                os.kill(pid, 0)
            except (ProcessLookupError, ValueError):
                discard = True
            except PermissionError:
                pass
        if discard:
            path.unlink(missing_ok=True)
            path.with_suffix('.log').unlink(missing_ok=True)


def expire_task(job_id):
    # A short-lived completion receipt, addressed only by an in-memory request ID.
    path = job_path(job_id)
    for _ in range(60):
        if not path.exists():
            return
        time.sleep(1)
    path.unlink(missing_ok=True)
    path.with_suffix('.log').unlink(missing_ok=True)


def publish_mp4(source, directory):
    """Expose only a finished MP4; never overwrite a user's existing file."""
    source, directory = Path(source), Path(directory)
    if source.suffix.lower() != '.mp4':
        raise ValueError('The output file must be MP4.')
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'.offlinevideo-{uuid.uuid4().hex}.part'
    try:
        shutil.copyfile(source, temporary)
        number = 0
        while True:
            name = source.name if not number else f'{source.stem} ({number}){source.suffix}'
            target = directory / name
            try:
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                number += 1
                continue
            os.close(descriptor)
            try:
                temporary.replace(target)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return target
    finally:
        temporary.unlink(missing_ok=True)


def capabilities():
    return {'downloader': importlib.util.find_spec('yt_dlp') is not None,
            'ffmpeg': bool(executable('ffmpeg')), 'javascript': bool(executable('node') or executable('deno')),
            'folder': str(download_directory()), 'picker': read_json(STATE / 'picker.json', {})}


def dispatch(message):
    action = message.get('action')
    if action == 'status':
        purge_history()
        return {'ok': True, 'capabilities': capabilities()}
    if action == 'task_status':
        path = job_path(message.get('id'))
        job = read_json(path, {})
        if not job:
            return {'ok': True, 'state': 'expired'}
        result = {'ok': True, 'state': job['status'], 'percent': job.get('percent')}
        if job.get('error'):
            result['error'] = job['error']
        if job['status'] in ('done', 'error'):
            path.unlink(missing_ok=True)
            path.with_suffix('.log').unlink(missing_ok=True)
        return result
    if action == 'choose_folder':
        picker = read_json(STATE / 'picker.json', {})
        if picker.get('state') == 'choosing' and time.time() - picker.get('started', 0) < 310:
            return {'ok': True}
        write_json(STATE / 'picker.json', {'state': 'choosing', 'started': time.time()})
        try:
            spawn(['--choose-folder'], 'folder-picker.log')
        except Exception as exc:
            write_json(STATE / 'picker.json', {'state': 'error', 'error': str(exc)})
            raise
        return {'ok': True}
    if action == 'download':
        url = validate_url(message.get('url'))
        quality = message.get('quality', '1080')
        if quality not in QUALITIES:
            raise ValueError('Unknown quality setting.')
        caps = capabilities()
        if not caps['downloader'] or not caps['ffmpeg']:
            raise ValueError('Install the helper and FFmpeg. See the project README.')
        # Browser retries must not create two workers for the same request.
        job_id = message.get('id')
        path = job_path(job_id)
        if path.exists():
            return {'ok': True, 'id': job_id}
        job = {'id': job_id, 'url': url, 'quality': quality, 'title': 'Preparing video',
               'created': time.time(), 'status': 'queued', 'percent': None,
               'directory': str(download_directory())}
        save(job)
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', job_id],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {'ok': True, 'id': job_id}
    if action == 'open_folder':
        directory = download_directory()
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/open', str(directory)], check=True, stdout=subprocess.DEVNULL)
        return {'ok': True}
    raise ValueError('Unknown command.')


def worker(job_id):
    job = read_job(job_id)
    job.update(status='downloading', pid=os.getpid())
    save(job)
    last_write = 0

    def progress(data):
        nonlocal last_write
        now = time.monotonic()
        if now - last_write < 0.6 and data['status'] != 'finished':
            return
        last_write = now
        total = data.get('total_bytes') or data.get('total_bytes_estimate')
        job.update(title=data.get('info_dict', {}).get('title') or job['title'],
                   status='processing' if data['status'] == 'finished' else 'downloading',
                   percent=min(100, round(data.get('downloaded_bytes', 0) * 100 / total, 1)) if total else None)
        save(job)

    def postprocess(data):
        job.update(status='processing', percent=None)
        save(job)

    class Logger:
        def debug(self, message):
            pass
        def warning(self, message):
            print(message, file=sys.stderr)
        def error(self, message):
            print(message, file=sys.stderr)

    try:
        import yt_dlp
        directory = Path(job.get('directory', str(DOWNLOADS)))
        directory.mkdir(parents=True, exist_ok=True)
        staging = tempfile.TemporaryDirectory(prefix='offlinevideo-')
        output = Path(staging.name)
        quality = job['quality']
        cap = '' if quality == 'best' else f'[height<=?{quality}]'
        runtimes = {}
        for name in ('node', 'deno'):
            if executable(name):
                runtimes[name] = {'path': executable(name)}
        options = {
            'format': f'bv*{cap}[ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/b{cap}[ext=mp4][vcodec^=avc1][acodec^=mp4a]/bv*{cap}+ba/b{cap}', 'merge_output_format': 'mp4/mkv',
            'outtmpl': str(output / '%(title).150B [%(id)s].%(ext)s'),
            'noplaylist': True, 'continuedl': True,
            'ffmpeg_location': executable('ffmpeg'),
            'js_runtimes': runtimes, 'socket_timeout': 30, 'retries': 3,
            'progress_hooks': [progress], 'postprocessor_hooks': [postprocess], 'logger': Logger(),
            'restrictfilenames': False, 'windowsfilenames': True,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(validate_url(job['url']), download=False)
            if not info or info.get('_type') in ('playlist', 'multi_video', 'compat_list') or 'entries' in info:
                raise ValueError('Use a link to an individual video instead of a playlist.')
            if info.get('is_live'):
                raise ValueError('This live stream is still running. Download the recording after it ends.')
            info = downloader.process_ie_result(info, download=True)
        files = [p for p in output.iterdir() if p.is_file() and p.suffix not in ('.part', '.ytdl', '.temp')]
        if not files:
            raise ValueError('The site did not return a video file.')
        job.update(status='processing', percent=None)
        save(job)
        files = [publish_mp4(compatible_mp4(path), directory) for path in files]
        job.update(status='done', title=info.get('title', job['title']), percent=100, compatible=True,
                   files=[str(p) for p in files])
    except Exception as exc:
        job.update(status='error', error=str(exc)[-1800:])
    finally:
        if 'staging' in locals():
            staging.cleanup()
    save(job)


def read_exact(stream, size):
    data = b''
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError('Incomplete message')
        data += chunk
    return data


def receive(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError('Incomplete header')
    length = struct.unpack('=I', header)[0]
    if not 0 < length <= 65536:
        raise ValueError('Message too large')
    result = json.loads(read_exact(stream, length))
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


def send(stream, message):
    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    stream.write(struct.pack('=I', len(data)) + data)
    stream.flush()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--choose-folder':
        choose_directory()
        return
    if len(sys.argv) == 2 and sys.argv[1] == '--purge-history':
        purge_history(all_finished=True)
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(sys.argv[2])
        expire_task(sys.argv[2])
        return
    try:
        message = receive(sys.stdin.buffer)
        if message is None:
            return
        response = dispatch(message)
    except Exception as exc:
        response = {'ok': False, 'error': str(exc)}
    send(sys.stdout.buffer, response)


if __name__ == '__main__':
    main()

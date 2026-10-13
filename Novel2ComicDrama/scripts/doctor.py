"""Exercise Python and media executables before opening the local UI."""
import subprocess
import sys

def main():
    print('Python:', sys.version.split()[0])
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or later is required.')
    for name in ('ffmpeg', 'ffprobe'):
        try:
            result = subprocess.run([name, '-version'], capture_output=True, timeout=15)
            if result.returncode:
                raise RuntimeError('nonzero exit')
            print(name + ': executable verified')
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise SystemExit(name + ' cannot execute. Install FFmpeg and add its bin directory to PATH.')

if __name__ == '__main__':
    main()

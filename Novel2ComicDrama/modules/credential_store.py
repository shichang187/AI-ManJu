"""User-bound Windows DPAPI; other platforms keep credentials in memory only."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import tempfile


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('此系统仅支持本次会话保存 API Key')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    dll = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; user scope, never LOCAL_MACHINE.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('安全凭据无法解密或保存，请重新输入 API Key')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        kernel.LocalFree(output.data)


class CredentialStore:
    def __init__(self, private_dir):
        self.directory = Path(private_dir) / 'credentials'
        self.memory = {}
        self.persistent = os.name == 'nt'

    @staticmethod
    def ref(provider, endpoint):
        return hashlib.sha256((provider + '\n' + endpoint.rstrip('/')).encode()).hexdigest()

    def _path(self, ref):
        if len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref):
            raise ValueError('安全凭据引用无效')
        return self.directory / (ref + '.dpapi')

    def put(self, ref, key):
        path = self._path(ref)
        if not self.persistent:
            self.memory[ref] = key
            return
        encrypted = crypt(key.encode('utf-8'))
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.directory, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def get(self, ref):
        path = self._path(ref)
        if not self.persistent:
            return self.memory.get(ref, '')
        if not path.exists():
            return ''
        if path.stat().st_size > 32768:
            raise ValueError('安全凭据损坏，请重新输入 API Key')
        try:
            return crypt(path.read_bytes(), decrypt=True).decode('utf-8')
        except (OSError, UnicodeError, ValueError):
            raise ValueError('安全凭据无法解密，请重新输入 API Key') from None

    def clear(self, ref):
        self.memory.pop(ref, None)
        self._path(ref).unlink(missing_ok=True)

"""Impide dos lectores sobre el mismo directorio de datos."""
import os
from .configuracion import ESTADO_DIR


class BloqueoLector:
    def __init__(self, directorio=None):
        self.directorio = directorio or ESTADO_DIR

    def __enter__(self):
        self.directorio.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.f = open(self.directorio / "lector.lock", "a+b")
        if os.fstat(self.f.fileno()).st_size == 0:
            self.f.write(b"0")
            self.f.flush()
        self.f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise RuntimeError("Ya hay un lector activo en esta carpeta. Detenlo primero.") from None
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        self.f.close()

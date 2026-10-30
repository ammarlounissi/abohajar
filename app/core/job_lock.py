"""قفل مشترك بين عاملي الفيديو والأوصاف: لا تعمل مهمتان ثقيلتان (ffmpeg وOllama) في وقت واحد على المعالج.

القفل flock على ملف، فيتحرر تلقائياً عند موت العملية. العامل يأخذه قبل claim ويفرجه بعد انتهاء المهمة،
فتنتظر المهمة الأخرى دورها وهي ما زالت queued (لا تُعدّ running ولا تتأثر باستعادة المهام المتوقفة)."""
import contextlib
import os
import tempfile

try:
    import fcntl
except ImportError:          # ويندوز: لا قفل، السلوك القديم
    fcntl = None

LOCK_PATH = os.environ.get("JOB_LOCK_FILE") or os.path.join(tempfile.gettempdir(), "hadjret-heavy-job.lock")


@contextlib.contextmanager
def try_acquire(path: str = None):
    """يرجع True إن حصل على القفل (ويبقيه حتى نهاية الكتلة)، وإلا False فوراً."""
    if fcntl is None:
        yield True
        return
    handle = open(path or LOCK_PATH, "a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()

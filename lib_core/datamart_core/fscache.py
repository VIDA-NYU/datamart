# This whole module exists as stand-alone, without the Prometheus metrics
# https://pypi.org/project/fslock/

import contextlib
import fcntl
import logging
import multiprocessing
import os
import prometheus_client
import shutil
import signal
import time


logger = logging.getLogger(__name__)


PROM_LOCK_ACQUIRE = prometheus_client.Histogram(
    'cache_lock_acquire_seconds',
    "Time to acquire lock on cache",
    ['type'],
    buckets=[1.0, 10.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0,
             float('inf')],
)
PROM_LOCKS_HELD = prometheus_client.Gauge(
    'cache_locks_held',
    "Number of locks on cache currently held",
    ['type'],
)

PROM_CACHE_HITS = prometheus_client.Counter(
    'cache_hits',
    "Number of cache lookups that hit, per cache directory",
    ['cache_dir'],
)
PROM_CACHE_MISSES = prometheus_client.Counter(
    'cache_misses',
    "Number of cache lookups that miss, per cache directory",
    ['cache_dir'],
)

PROM_CACHE_HITS.labels('/cache/datasets').inc(0)
PROM_CACHE_MISSES.labels('/cache/datasets').inc(0)
PROM_CACHE_HITS.labels('/cache/aug').inc(0)
PROM_CACHE_MISSES.labels('/cache/aug').inc(0)


@contextlib.contextmanager
def timeout_syscall(seconds):
    """Interrupt a system-call after a time (main thread only).

    Warning: this only works from the main thread! Trying to use this on
    another thread will cause the call to not timeout, and the main thread will
    receive an InterruptedError instead!

    Example::

        with timeout_syscall(5):
            try:
                socket.connect(...)
            except InterruptedError:
                raise ValueError("This host does not respond in time")
    """
    def timeout_handler(signum, frame):
        raise InterruptedError

    original_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)


def _lock_process(pipe, filepath, exclusive, timeout=None):
    """Locking function, runs in a subprocess.

    We run the locking in a subprocess so that we are the main thread
    (required to use SIGALRM) and to avoid spurious unlocking on Linux (which
    can happen if a different file descriptor for the same file gets closed,
    even by another thread).
    """
    try:
        # Reset signal handlers
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        # Open the file
        mode = os.O_RDONLY | os.O_CREAT if exclusive else os.O_RDONLY
        try:
            fd = os.open(filepath, mode)
        except FileNotFoundError:
            pipe.send('NOTFOUND')
            return

        # Lock it
        op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if timeout is None:
            fcntl.flock(fd, op)
        elif timeout == 0:
            try:
                fcntl.flock(fd, op | fcntl.LOCK_NB)
            except BlockingIOError:
                pipe.send('TIMEOUT')
                return
        else:
            with timeout_syscall(timeout):
                try:
                    fcntl.flock(fd, op)
                except InterruptedError:
                    pipe.send('TIMEOUT')
                    return
        pipe.send('LOCKED')
    except Exception:
        pipe.send('ERROR')
        raise

    # Wait for unlock message then exit
    assert pipe.recv() == 'UNLOCK'

    # Exiting releases the lock


# Using the 'fork' method causes deadlocks because other threads acquire locks
_mp_context = multiprocessing.get_context('spawn')


@contextlib.contextmanager
def _lock(filepath, exclusive, timeout=None):
    type_ = "exclusive" if exclusive else "shared"

    started = False
    locked = False
    pipe, pipe2 = _mp_context.Pipe()
    proc = _mp_context.Process(
        target=_lock_process,
        args=(pipe2, filepath, exclusive, timeout),
    )
    try:
        with PROM_LOCK_ACQUIRE.labels(type_).time():
            proc.start()
            started = True

            out = pipe.recv()
            if out == 'LOCKED':
                logger.info("Acquired %s lock: %r", type_, filepath)
                locked = True
                PROM_LOCKS_HELD.labels(type_).inc()
            elif out == 'TIMEOUT':
                logger.debug("Timeout getting %s lock: %r", type_, filepath)
                raise TimeoutError
            elif out == 'NOTFOUND':
                raise FileNotFoundError
            else:
                logger.error("Error getting %s lock: %r", type_, filepath)
                raise OSError("Error getting %s lock: %r", type_, filepath)

        yield
    finally:
        if not started:
            return
        logger.debug("Releasing %s lock: %r", type_, filepath)
        pipe.send('UNLOCK')
        proc.join(10)
        if proc.exitcode is None:
            start = time.perf_counter()
            proc.join(3 * 60)
            logger.critical("Releasing %s lock took %.2fs: %r",
                            type_, time.perf_counter() - start, filepath)
        if proc.exitcode != 0:
            logger.critical("Failed (%r) to release %s lock: %r",
                            proc.exitcode, type_, filepath)
            raise SystemExit("Failed (%r) to release %s lock: %r" % (
                proc.exitcode, type_, filepath,
            ))
        logger.info("Released %s lock: %r", type_, filepath)
        if locked:
            PROM_LOCKS_HELD.labels(type_).dec()


def FSLockExclusive(filepath, timeout=None):
    """Get an exclusive lock.

    The file is created if it doesn't exist.
    """
    return _lock(filepath, True, timeout=timeout)


def FSLockShared(filepath, timeout=None):
    """Get a shared lock.

    :raises FileNotFoundError: if the file doesn't exist.
    """
    return _lock(filepath, False, timeout=timeout)


@contextlib.contextmanager
def cache_get_or_set(cache_dir, key, create_function, cache_invalid=False):
    """This function is a file cache safe for multiple processes (locking).

    It is used like so::

        # This function is called to create the entry if it doesn't exist
        def create_it(tmp_path):
            # In this function, the path is locked with an exclusive lock
            # `tmp_path` will be renamed to the cache path on success
            with open(tmp_path, 'w') as fp:
                fp.write('%d\n' % long_computation())

        with cache_get_or_set('/tmp/cache', 'key123', create_it) as entry_path:
            # In this with-block, the path is locked with a shared lock, so it
            # won't be changed or removed
            with open(entry_path) as fp:
                print(fp.read())
    """
    entry_path = os.path.join(cache_dir, key + '.cache')
    lock_path = os.path.join(cache_dir, key + '.lock')
    temp_path = os.path.join(cache_dir, key + '.temp')
    metric_set = False
    while True:
        if not cache_invalid:
            with contextlib.ExitStack() as lock:
                try:
                    lock.enter_context(FSLockShared(lock_path))
                except FileNotFoundError:
                    pass
                else:
                    if os.path.exists(entry_path):
                        if not metric_set:
                            metric_set = True
                            PROM_CACHE_HITS.labels(cache_dir).inc(1)

                        # Update time on the file
                        with open(lock_path, 'a'):
                            pass

                        # Entry exists and we have it locked, return it
                        yield entry_path
                        return
                    # Entry was removed while we waited -- we'll try creating

        # Whether we do it below or conflict, entry will have been re-created
        cache_invalid = False

        with FSLockExclusive(lock_path):
            if os.path.exists(entry_path):
                # Cache was created while we waited
                # We can't downgrade to a shared lock, so restart
                continue
            else:
                # Remote temporary file
                if os.path.isdir(temp_path):
                    shutil.rmtree(temp_path)
                elif os.path.isfile(temp_path):
                    os.remove(temp_path)

                try:
                    if not metric_set:
                        metric_set = True
                        PROM_CACHE_MISSES.labels(cache_dir).inc(1)
                    # Cache doesn't exist and we have it locked -- create
                    create_function(temp_path)
                except BaseException:
                    # Creation failed, clean up before unlocking!
                    if os.path.isdir(temp_path):
                        shutil.rmtree(temp_path)
                    elif os.path.isfile(temp_path):
                        os.remove(temp_path)
                    os.remove(lock_path)
                    raise
                else:
                    # Rename it to destination
                    os.rename(temp_path, entry_path)

                # We can't downgrade to a shared lock, so restart
                continue


def clear_cache(cache_dir, should_delete=None, only_if_possible=True):
    """Function used to safely clear a cache.

    Directory currently locked by other processes will be
    """
    if should_delete is None:
        should_delete = lambda *, key: True

    files = sorted(
        f for f in os.listdir(cache_dir)
        if f.endswith('.cache')
    )
    logger.info("Cleaning cache, %d entries in %r", len(files), cache_dir)

    # Loop while there are entries to delete
    timeout = 0
    while files:
        not_deleted = []
        for fname in files:
            key = fname[:-6]
            if not should_delete(key=key):
                logger.info("Skipping entry: %r", key)
                continue
            try:
                delete_cache_entry(cache_dir, key, timeout=timeout)
            except TimeoutError:
                logger.warning("Entry is locked: %r", key)
                not_deleted.append(fname)
                continue
        files = not_deleted
        logger.info("%d entries left", len(files))
        if only_if_possible:
            break  # Give up on deleting the ones that are left
        else:
            timeout = 60  # Retry with a non-zero timeout


def delete_cache_entry(cache_dir, key, timeout=None):
    entry_path = os.path.join(cache_dir, key + '.cache')
    lock_path = os.path.join(cache_dir, key + '.lock')
    temp_path = os.path.join(cache_dir, key + '.temp')
    logger.info("Locking entry: %r", key)
    with contextlib.ExitStack() as lock:
        lock.enter_context(  # Might raise TimeoutError
            FSLockExclusive(lock_path, timeout=timeout),
        )
        if os.path.exists(entry_path):
            logger.info("Deleting entry: %r", key)
            if os.path.isfile(entry_path):
                os.remove(entry_path)
            else:
                shutil.rmtree(entry_path)
            os.remove(lock_path)
        else:
            os.remove(lock_path)
        if os.path.exists(temp_path):
            logger.info("Deleting temporary file: %r", key + '.temp')
            if os.path.isfile(entry_path):
                os.remove(temp_path)
            else:
                shutil.rmtree(temp_path)

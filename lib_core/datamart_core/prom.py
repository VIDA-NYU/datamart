import contextlib
from prometheus_async.aio import time as prom_async_time


class PromMeasureRequest(object):
    def __init__(self, count, time):
        self.count = count
        self.time = time

    def _wrap(self, *labels, timer):
        if labels:
            counter = self.count.labels(*labels)
        else:
            counter = self.count
        if labels:
            timer = timer(self.time.labels(*labels))
        else:
            timer = timer(self.time)

        # Initialize count
        counter.inc(0)

        def decorator(func):
            @contextlib.wraps(func)
            def wrapper(*args, **kwargs):
                # Count requests
                counter.inc()
                return func(*args, **kwargs)

            return timer(wrapper)

        return decorator

    def sync(self, *labels):
        return self._wrap(*labels, timer=lambda metric: metric.time())

    def async_(self, *labels):
        return self._wrap(*labels, timer=lambda metric: prom_async_time(metric))

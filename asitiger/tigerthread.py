import threading

from asitiger.tigercontroller import TigerController


class TigerThread(threading.Thread):
    def __init__(self, port):
        super().__init__()
        self._tiger = TigerController.from_serial_port(port=port)
        self._lock = threading.Lock()

    def run(self):
        pass  # You may implement this if you want the thread to perform some action continuously

    def __getattr__(self, name):
        attr = getattr(self._tiger, name)
        if callable(attr):
            def method_with_lock(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return method_with_lock
        else:
            return attr

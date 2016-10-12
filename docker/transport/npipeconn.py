import six
import requests.adapters

from .. import constants
from .npipesocket import NpipeSocket

if six.PY3:
    import http.client as httplib
else:
    import httplib

try:
    import requests.packages.urllib3 as urllib3
except ImportError:
    import urllib3

RecentlyUsedContainer = urllib3._collections.RecentlyUsedContainer


# Monkey-patching: urllib3.is_connection_dropped calls select() on our
# NpipeSocket object which breaks on Windows because it only works on
# (real) sockets.
def is_connection_dropped(*args, **kwargs):
    return False


def check_socket_type(f):
    def wrapped(conn):
         sock = getattr(conn, 'sock', False)
        if sock and isinstance(sock, NpipeSocket):
            return sock._closed
        return f(conn)
    return wrapped

urllib3.util.connection.is_connection_dropped = check_socket_type(
    urllib3.util.connection.is_connection_dropped
)


class NpipeHTTPConnection(httplib.HTTPConnection, object):
    def __init__(self, npipe_path, timeout=60):
        super(NpipeHTTPConnection, self).__init__(
            'localhost', timeout=timeout
        )
        self.npipe_path = npipe_path
        self.timeout = timeout

    def connect(self):
        sock = NpipeSocket()
        sock.settimeout(self.timeout)
        sock.connect(self.npipe_path)
        self.sock = sock


class NpipeHTTPConnectionPool(urllib3.connectionpool.HTTPConnectionPool):
    def __init__(self, npipe_path, timeout=60, maxsize=10):
        super(NpipeHTTPConnectionPool, self).__init__(
            'localhost', timeout=timeout, maxsize=maxsize
        )
        self.npipe_path = npipe_path
        self.timeout = timeout

    def _new_conn(self):
        return NpipeHTTPConnection(
            self.npipe_path, self.timeout
        )


class NpipeAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, base_url, timeout=60,
                 pool_connections=constants.DEFAULT_NUM_POOLS):
        self.npipe_path = base_url.replace('npipe://', '')
        self.timeout = timeout
        self.pools = RecentlyUsedContainer(
            pool_connections, dispose_func=lambda p: p.close()
        )
        super(NpipeAdapter, self).__init__()

    def get_connection(self, url, proxies=None):
        with self.pools.lock:
            pool = self.pools.get(url)
            if pool:
                return pool

            pool = NpipeHTTPConnectionPool(
                self.npipe_path, self.timeout
            )
            self.pools[url] = pool

        return pool

    def request_url(self, request, proxies):
        # The select_proxy utility in requests errors out when the provided URL
        # doesn't have a hostname, like is the case when using a UNIX socket.
        # Since proxies are an irrelevant notion in the case of UNIX sockets
        # anyway, we simply return the path URL directly.
        # See also: https://github.com/docker/docker-py/issues/811
        return request.path_url

    def close(self):
        self.pools.clear()

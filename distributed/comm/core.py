from __future__ import print_function, division, absolute_import

from abc import ABCMeta, abstractmethod, abstractproperty
from datetime import timedelta
import logging

from six import with_metaclass

from tornado import gen
from tornado.ioloop import IOLoop

from ..config import config
from ..metrics import time
from .addressing import parse_address


logger = logging.getLogger(__name__)

# Connector instances

connectors = {
    #'tcp': ...,
    #'zmq': ...,
    }


# Listener classes

listeners = {
    #'tcp': ...,
    # 'zmq': ...,
    }


class CommClosedError(IOError):
    pass


class Comm(with_metaclass(ABCMeta)):
    """
    A message-oriented communication object, representing an established
    communication channel.  There should be only one reader and one
    writer at a time: to manage current communications, even with a
    single peer, you must create distinct ``Comm`` objects.

    Messages are arbitrary Python objects.  Concrete implementations
    of this class can implement different serialization mechanisms
    depending on the underlying transport's characteristics.
    """

    # XXX add set_close_callback()?

    @abstractmethod
    def read(self):
        """
        Read and return a message (a Python object).

        This method is a coroutine.
        """

    @abstractmethod
    def write(self, msg):
        """
        Write a message (a Python object).

        This method is a coroutine.
        """

    @abstractmethod
    def close(self):
        """
        Close the communication cleanly.  This will attempt to flush
        outgoing buffers before actually closing the underlying transport.

        This method is a coroutine.
        """

    @abstractmethod
    def abort(self):
        """
        Close the communication immediately and abruptly.
        Useful in destructors or generators' ``finally`` blocks.
        """

    @abstractmethod
    def closed(self):
        """
        Return whether the stream is closed.
        """

    @abstractproperty
    def peer_address(self):
        """
        The peer's address.  For logging and debugging purposes only.
        """


class Listener(with_metaclass(ABCMeta)):

    @abstractmethod
    def start(self):
        """
        Start listening for incoming connections.
        """

    @abstractmethod
    def stop(self):
        """
        Stop listening.  This does not shutdown already established
        communications, but prevents accepting new ones.
        """

    @abstractproperty
    def listen_address(self):
        """
        The listening address as a URI string.
        """

    @abstractproperty
    def contact_address(self):
        """
        An address this listener can be contacted on.  This can be
        different from `listen_address` if the latter is some wildcard
        address such as 'tcp://0.0.0.0:123'.
        """

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


@gen.coroutine
def connect(addr, timeout=3, deserialize=True):
    """
    Connect to the given address (a URI such as ``tcp://127.0.0.1:1234``)
    and yield a ``Comm`` object.  If the connection attempt fails, it is
    retried until the *timeout* is expired.
    """
    scheme, loc = parse_address(addr)
    connector = connectors.get(scheme)
    if connector is None:
        raise ValueError("unknown scheme %r in address %r" % (scheme, addr))

    start = time()
    deadline = start + timeout
    error = None

    def _raise(error):
        error = error or "connect() didn't finish in time"
        msg = ("Timed out trying to connect to %r after %s s: %s"
               % (addr, timeout, error))
        raise IOError(msg)

    while True:
        try:
            future = connector.connect(loc, deserialize=deserialize)
            comm = yield gen.with_timeout(timedelta(seconds=deadline - time()),
                                          future,
                                          quiet_exceptions=EnvironmentError)
        except EnvironmentError as e:
            error = str(e)
            if time() < deadline:
                yield gen.sleep(0.01)
                logger.debug("sleeping on connect")
            else:
                _raise(error)
        except gen.TimeoutError:
            _raise(error)
        else:
            break

    raise gen.Return(comm)


def listen(addr, handle_comm, deserialize=True):
    """
    Create a listener object with the given parameters.  When its ``start()``
    method is called, the listener will listen on the given address
    (a URI such as ``tcp://0.0.0.0``) and call *handle_comm* with a
    ``Comm`` object for each incoming connection.

    *handle_comm* can be a regular function or a coroutine.
    """
    scheme, loc = parse_address(addr)
    listener_class = listeners.get(scheme)
    if listener_class is None:
        raise ValueError("unknown scheme %r in address %r" % (scheme, addr))

    return listener_class(loc, handle_comm, deserialize)
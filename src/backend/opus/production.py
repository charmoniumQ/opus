'''
This module contains the implementation of
various types of producer classes. It also
contains the implementation of the communication
classes the OPUS backend uses to receive provenance
data from connected clients.
'''

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)


import errno
import logging
import select
import socket
import struct
import threading
import time
import traceback

from . import common_utils, ipc, messaging, multisocket, uds_msg_pb2
from .exception import OPUSException


def format_stack():
    stack = traceback.extract_stack()
    # cut off last elem
    stack = stack[::-1]
    return " -> ".join(":".join([module, line]) for module, line, function, _ in stack)

def get_credentials(client_fd):
    '''Reads the peer credentials from a UDS descriptor'''
    if not hasattr(get_credentials, "SO_PEERCRED"):
        get_credentials.SO_PEERCRED = 17
    credentials = client_fd.getsockopt(socket.SOL_SOCKET,
                                       get_credentials.SO_PEERCRED,
                                       struct.calcsize(str('3i')))
    pid, uid, gid = struct.unpack('3i', credentials)
    return pid, uid, gid


def mono_time_in_nanosecs():
    '''Returns a monotonic time if available, else returns 0'''
    ret_time = 0

    if hasattr(time, 'clock_gettime'):
        try:
            ret_time = int(time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 1e+9)
        except OSError as exc:
            logging.error("Error: %d, Message: %s", exc.errno, exc.strerror)

    return ret_time


def create_close_conn_obj(sock_obj, pid):
    '''Returns objects to mark a client connection close'''
    if __debug__:
        logging.debug("Creating close message for %d", sock_obj.fileno())

    gen_msg = uds_msg_pb2.GenericMessage()
    gen_msg.msg_type = uds_msg_pb2.DISCON
    gen_msg.msg_desc = "Client socket: %d disconnected" % (sock_obj.fileno())

    header = messaging.Header()
    header.timestamp = mono_time_in_nanosecs()
    header.pid = pid
    header.tid = pid  # We dont have the tid
    header.payload_type = uds_msg_pb2.GENERIC_MSG
    header.payload_len = gen_msg.ByteSize()
    header.sys_time = int(time.time())

    return header.dumps(), gen_msg.SerializeToString()


class SockReader(object):
    '''Reads header and payload from a non-blocking socket'''

    def __init__(self, sock_obj):
        '''Initialize data members'''
        self.sock_obj = sock_obj
        self.buf_data = b''
        self.header = None

    def get_message(self):
        '''Reads message from socket'''

        # Read header
        if self.header is None:
            remaining_len = messaging.Header.length - len(self.buf_data)

            status_code = self._fill_buffer(remaining_len)
            if status_code != MultiCommunicationManager.StatusCode.success:
                return status_code, None, None

            # If header is fully received, construct the header object
            if len(self.buf_data) == messaging.Header.length:
                self.header = messaging.Header()
                self.header.loads(self.buf_data)
                if __debug__:
                    logging.debug("Header: %s", self.header.__str__())

        # Account for header data already present
        hdr_len = messaging.Header.length
        remaining_len = self.header.payload_len - (len(self.buf_data) -
                                                   hdr_len)

        # Read the payload
        status_code = self._fill_buffer(remaining_len)
        if status_code != MultiCommunicationManager.StatusCode.success:
            return status_code, None, None

        hdr_buf = self.buf_data[:hdr_len]
        pay_buf = self.buf_data[hdr_len:]

        # Deserialization only needed for debugging during development
        if __debug__:
            if self.header.payload_type == uds_msg_pb2.AGGREGATION_MSG:
                logging.debug("Payload: %s", repr(pay_buf))
            else:
                payload = common_utils.get_payload_type(self.header)
                logging.debug("pay_buf: %s", repr(pay_buf))
                payload.ParseFromString(pay_buf)
                logging.debug("Payload: %s", payload.__str__())

        # Reset data members
        self.buf_data = b''
        self.header = None

        return status_code, hdr_buf, pay_buf

    def _fill_buffer(self, remaining_len):
        '''Calls receive and appends buffer'''
        tmp_buf, status_code = self._receive(self.sock_obj, remaining_len)
        self.buf_data += tmp_buf
        return status_code

    def _receive(self, sock_obj, size):
        '''Receives data for a given size from a socket'''
        buf = b''
        status_code = MultiCommunicationManager.StatusCode.success
        while size > 0:
            try:
                data = sock_obj.recv(size)
                if data == b'':
                    status_code = \
                        MultiCommunicationManager.StatusCode.close_connection
                    break
            except socket.error as exc:
                if exc.errno == errno.EAGAIN or exc.errno == errno.EWOULDBLOCK:
                    status_code = \
                        MultiCommunicationManager.StatusCode.try_again_later
                elif exc.errno == errno.EINTR:
                    logging.error("Error: %d, Message: %s",
                                  exc.errno, exc.strerror)
                    continue
                else:
                    logging.error("Error: %d, Message: %s",
                                  exc.errno, exc.strerror)
                    status_code = \
                        MultiCommunicationManager.StatusCode.close_connection
                break
            buf += data
            size -= len(data)
        return buf, status_code

    def get_sock_obj(self):
        '''Returns socket object'''
        return self.sock_obj

    def close(self):
        '''Closes the underlying socket object'''
        self.sock_obj.close()


class CommunicationManager(object):
    '''Base class for the communication manager class'''
    def __init__(self):
        '''Initialize data members'''
        super(CommunicationManager, self).__init__()

    def close(self):
        '''Override this in the derived class'''
        pass

    def do_poll(self):
        '''Override this in the derived class'''
        pass


class MultiCommunicationManager(CommunicationManager):
    '''multisocket specific server implementation'''
    StatusCode = common_utils.enum(success=0,
                                   close_connection=100,
                                   try_again_later=101)

    def __init__(self, addr,
                 max_conn=10, select_timeout=5.0,
                 *args, **kwargs):
        '''Initialize the class members'''
        super(MultiCommunicationManager, self).__init__(*args, **kwargs)
        self.input_client_map = {}  # fd -> SockReader
        self.pid_map = {}  # pid to list of sock objects map
        self.addr = addr  # Configurable
        self.max_server_conn = max_conn  # Configurable
        self.select_timeout = select_timeout  # Configurable
        self.server_socket = None

        try:
            self.server_socket = multisocket.MultiFamilySocket(
                socket.SOCK_STREAM)
            self.server_socket.bind(self.addr)
            self.server_socket.listen(self.max_server_conn)
        except socket.error as err:
            if self.server_socket:
                self.server_socket.close()
            logging.error("Error: %s %s", str(err), format_stack())
            raise OPUSException("socket error")
        self.server_socket.setblocking(0)  # Make the socket non-blocking
        self.epoll = select.epoll()
        self.epoll.register(self.server_socket.fileno(),
                            select.EPOLLIN | select.EPOLLERR)

    def do_poll(self):
        '''Returns a list of tuples for all ready file descriptors'''
        ret_list = []  # List of tuples of form (header, payload)

        try:
            event_list = self.epoll.poll(self.select_timeout)
        except IOError as err:
            logging.error("Error: %s %s", str(err), format_stack())
            return ret_list

        if not event_list:
            if __debug__:
                logging.debug("epoll timed out")
            return ret_list

        for fileno, event in event_list:
            if fileno == self.server_socket.fileno():
                self._handle_new_connection()
            elif event & select.EPOLLIN:
                sock_obj = self.input_client_map[fileno].get_sock_obj()
                self._handle_client(sock_obj, ret_list)
            elif event & select.EPOLLHUP:
                if __debug__:
                    logging.debug("Got an EPOLLHUP event")
                sock_obj = self.input_client_map[fileno].get_sock_obj()
                self._handle_close_connection(sock_obj, ret_list)
        return ret_list

    def _handle_client(self, sock_obj, ret_list):
        '''Receives data from client or closes the client connection'''
        sock_rdr = self.input_client_map[sock_obj.fileno()]
        status_code, header_buf, payload_buf = sock_rdr.get_message()

        if status_code == self.StatusCode.success:
            if __debug__:
                logging.debug("Got valid data")
            ret_list += [(header_buf, payload_buf)]
        elif status_code == self.StatusCode.close_connection:
            self._handle_close_connection(sock_obj, ret_list)
        elif status_code == self.StatusCode.try_again_later:
            if __debug__:
                logging.debug("Will try again later")

    def _handle_close_connection(self, sock_obj, ret_list):
        '''Handles close event or hang up event on the client socket'''
        self.epoll.unregister(sock_obj.fileno())
        if sock_obj.fileno() in self.input_client_map:
            del self.input_client_map[sock_obj.fileno()]

        pid, _, _ = get_credentials(sock_obj)
        if pid in self.pid_map:
            sock_list = self.pid_map[pid]
            sock_list.remove(sock_obj)
            if len(sock_list) == 0:
                del self.pid_map[pid]
                ret_list.append(tuple(create_close_conn_obj(sock_obj, pid)))

        if __debug__:
            logging.debug('closing socket: %d', sock_obj.fileno())
        sock_obj.close()

    def _handle_new_connection(self):
        '''Accepts and adds the new connection to the fd list'''
        client_fd, _ = self.server_socket.accept()
        pid, uid, gid = get_credentials(client_fd)
        if __debug__:
            logging.debug("Got a new connection from"
                          " pid: %d, uid: %d, gid: %d",
                          pid, uid, gid)
        client_fd.setblocking(0)  # Make the socket non-blocking
        self.epoll.register(client_fd.fileno(),
                            select.EPOLLIN | select.EPOLLERR | select.EPOLLHUP)

        sock_rdr = SockReader(client_fd)  # Instantiate a SockReader object
        self.input_client_map[client_fd.fileno()] = sock_rdr

        if pid in self.pid_map:
            self.pid_map[pid] += [client_fd]
        else:
            self.pid_map[pid] = [client_fd]

    def detach(self, pid):
        if pid not in self.pid_map:
            return None, []
        else:
            sock_objs = self.pid_map[pid][:]
            ret_list = []
            for sock in sock_objs:
                self._handle_close_connection(sock, ret_list)
            return len(sock_objs), ret_list

    def ps(self):
        return {pid: len(threads)
                for pid, threads in self.pid_map.items()}

    def close(self):
        '''Close all connections and cleanup'''
        self.epoll.unregister(self.server_socket.fileno())
        self.server_socket.close()
        for fileno in self.input_client_map:
            self.epoll.unregister(fileno)
            self.input_client_map[fileno].close()


class Producer(threading.Thread):
    '''Base class for the producer thread'''
    def __init__(self, pf_queue, router):
        '''Initialize class data members'''
        super(Producer, self).__init__()
        self.pf_queue = pf_queue
        self.node = ipc.Worker(ident="PRODUCER",
                               router=router,
                               handler=self._command)
        self.node.run_forever()
        self.msg = None
        self.ret = None
        self.msg_waiting = threading.Event()
        self.ret_done = threading.Event()
        self.stop_event = threading.Event()
        self.daemon = True

    def _command(self, msg):
        self.msg = msg
        self.msg_waiting.set()
        self.ret_done.wait()
        self.ret_done.clear()
        return self.ret

    def run(self):
        '''Override in the derived class'''
        pass

    def _send_data_to_fetcher(self, msg_list):
        '''Enqueues messages on the producer fetcher queue'''
        self.pf_queue.enqueue(msg_list)

    def do_shutdown(self):
        '''Shutdown the thread gracefully'''
        if __debug__:
            logging.debug("Shutting down thread....")
        self.stop_event.set()
        try:
            self.join(common_utils.THREAD_JOIN_SLACK)
        except RuntimeError as exc:
            logging.error("Failed to shutdown thread sucessfully.")
            logging.error(exc)
            return False
        return not self.isAlive()


class SocketProducer(Producer):
    '''Implementation of a socket producer class'''
    def __init__(self, comm_mgr_type, comm_mgr_args, *args, **kwargs):
        '''Initialize the class data members'''
        super(SocketProducer, self).__init__(*args, **kwargs)
        try:
            self.comm_manager = common_utils.meta_factory(CommunicationManager,
                                                          comm_mgr_type,
                                                          **comm_mgr_args)
        except common_utils.InvalidTagException as err_msg:
            raise OPUSException(err_msg.msg)

    def run(self):
        '''Spin until thread stop event is set'''
        while not self.stop_event.isSet():
            msg_list = self.comm_manager.do_poll()

            if self.msg_waiting.is_set():
                self.msg_waiting.clear()
                additional_msgs = self._process_command(self)
                msg_list += additional_msgs
                self.ret_done.set()

            if not msg_list:
                if __debug__:
                    logging.debug("No message to be logged")
            else:
                if __debug__:
                    logging.debug("Calling enqueue data on queue")
                self._send_data_to_fetcher(msg_list)
        self.comm_manager.close()

    def do_shutdown(self):
        '''Shutdown the thread gracefully'''
        return super(SocketProducer, self).do_shutdown()

    def _process_command(self, msg):
        '''Handles a command message from the command and control system.'''
        cmd = self.msg.cont
        if cmd['cmd'] == "ps":
            self.ret = {"success": True,
                        "pid_map": self.comm_manager.ps()}
        elif cmd['cmd'] == "detach":
            if 'pid' in cmd:
                res, ret_msg = self.comm_manager.detach(cmd['pid'])
                if res is not None:
                    self.ret = {"success": True,
                                "msg": "Success. {:d} connections "
                                "closed.".format(res)}
                    return ret_msg
                else:
                    self.ret = {"success": False,
                                "msg": "Pid {:d} not connected to "
                                "OPUS.".format(cmd['pid'])}
            else:
                self.ret = {"success": False,
                            "msg": "Missing pid argument."}
        else:
            self.ret = {"success": False,
                        "msg": "%s is not a valid command." % cmd['cmd']}
        return []

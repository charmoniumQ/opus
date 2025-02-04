# -*- coding: utf-8 -*-
'''
Utilities for manipulation of command control messages.
'''

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

import json
import socket
import struct

from . import multisocket
from .exception import BackendConnectionError

CC_HDR = struct.Struct(str("@I"))

def send_cc_msg(sock, msg):
    '''Sends a command control message over the socket sock.'''
    msg_txt = json.dumps(msg)
    buf = CC_HDR.pack(len(msg_txt))
    buf += msg_txt
    sock.send(buf)


def __recv(sock, data_len, timeout):
    '''Recieves data of length data_len from socket sock.
    If the read from the socket fails at any point a IOError is thrown.'''
    buf = []
    size = data_len
    sock.settimeout(timeout)
    while size > 0:
        tmp = sock.recv(data_len)
        if tmp == str(""):
            raise IOError()
        buf += [tmp]
        size -= len(tmp)
    return str("").join(buf)


def recv_cc_msg(sock, timeout):
    '''Receives a single command control message from the given socket sock.'''
    hdr_buf = __recv(sock, CC_HDR.size, timeout)
    pay_len = CC_HDR.unpack(hdr_buf)[0]
    pay_buf = __recv(sock, pay_len, timeout)
    pay = json.loads(pay_buf)
    return pay


class CommandConnectionHelper(object):
    '''Manages a connection to the backend and provides helpers for making
    requests.'''
    def __init__(self, addr):
        self.addr = addr

    def make_request(self, msg):
        '''Sends a request message to the backend and retrieves a response.'''
        try:
            conn = multisocket.MultiFamilySocket(socket.SOCK_STREAM)
            conn.connect(self.addr)
        except IOError as exc:
            raise BackendConnectionError("Failed to make contact with "
                                         "the backend: %s" % exc)

        try:
            send_cc_msg(conn, msg)
        except IOError as exc:
            raise BackendConnectionError("Failed to send message to backend:"
                                         " %s" % exc)
        try:
            ret = recv_cc_msg(conn, 5.0)
        except IOError as exc:
            raise BackendConnectionError("Failed to receive message from"
                                         " backend: %s" % exc)
        except TimeoutError:
            raise BackendConnectionError("Recv timed out")
        conn.close()
        return ret

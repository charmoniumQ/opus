# -*- coding: utf-8 -*-
'''
TODO
'''
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)


class ClientQueryControl(object):
    client_qry_methods = {}

    @classmethod
    def register_query_method(cls, query_method):
        def wrap(method):
            cls.client_qry_methods[query_method] = method
            return method
        return wrap

    @classmethod
    def exec_method(cls, db_iface, msg):
        if msg['qry_method'] in cls.client_qry_methods:
            logging.info("opus.query.ClientQueryControl: method: %s, args: %s", repr(msg['qry_method']), repr(msg['qry_args']))
            return cls.client_qry_methods[msg['qry_method']](db_iface,
                                                             msg['qry_args'])
        else:
            return {"success": False, "msg": "Invalid query command"}

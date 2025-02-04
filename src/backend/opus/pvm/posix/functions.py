# -*- coding: utf-8 -*-
'''
Contains all of the function mappings of the posix implementation. Controls the
association between posix functions and python code that implements their PVM
semantics.
'''

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)


import fcntl
import logging
import pkg_resources
import os
import sys

try:
    import yaml
except ImportError:
    print("YAML module is not present!")
    print("Please install the PyYAML module.")
    sys.exit(1)


from ... import pvm
from . import actions, process, utils
from ... import common_utils, storage, traversal, uds_msg_pb2
from ...exception import MissingMappingError


def _parse_mapping(msg, mapping):
    '''Given a message and an argument mapping, retrieve the value of that
    argument.'''
    if mapping[0] == "msg_arg":
        args = utils.parse_kvpair_list(msg.args)
        return args[mapping[1]]
    elif mapping[0] == "msg_field":
        if msg.HasField(mapping[1]):
            return getattr(msg, mapping[1])
        else:
            return None
    elif mapping[0] == "ret_val":
        return str(msg.ret_val)
    elif mapping[0] == "const":
        return str(mapping[1])


def wrap_action(action, arg_map):
    '''Converts an item from the ActionMap into a lambda taking
    storage interface, process node and a msg.'''
    def fun(db_iface, proc_node, msg):
        '''Wrapper internal. '''
        arg_set = {}
        for k, val in arg_map.items():
            arg_set[k] = _parse_mapping(msg, val)
        return actions.ActionMap.call(action, msg.error_num,
                                      db_iface, proc_node, **arg_set)
    return fun


class FuncController(object):
    '''Mapping for function names to definitions.'''
    funcs = {}
    func_map = {}

    @classmethod
    def load(cls, func_file):
        '''Loads a YAML action specification from func_file.'''
        try:
            with pkg_resources.resource_stream("opus.pvm.posix",
                                               func_file) as conf:
                cls.func_map = yaml.safe_load(conf)
                for func_name, mapping in cls.func_map.items():
                    cls.register(func_name, wrap_action(**mapping))
        except IOError:
            logging.error("Failed to read in config file.")
            raise

    @classmethod
    def register(cls, name, func):
        '''Register func with key name.'''
        cls.funcs[name] = func

    @classmethod
    def call(cls, name, *args):
        '''Calls the method associated with name with any subsequent
        arguments.'''
        if name in cls.funcs:
            logging.info("opus.pvm.posix.functions.FuncController.call: %s", name)
            return cls.funcs[name](*args)
        else:
            logging.error("Failed to find mapping for function %s.", name)
            raise MissingMappingError()

    @classmethod
    def dec(cls, name):
        '''Declares the wrapped function as representing the given name.'''
        def wrapper(fun):
            '''Decorator internals.'''
            FuncController.register(name, fun)
            return fun
        return wrapper


FuncController.load("pvm.yaml")


def get_fd_from_msg(msg):
    '''Given a function message retrieves the filedescriptor it operates on.'''
    mapping = FuncController.func_map[msg.func_name]['arg_map']['filedes']
    return _parse_mapping(msg, mapping)


def load_cache(db_iface, loc_name, proc_node, mono_time):
    '''Loads the event cache data for a given local node.'''
    logging.debug("Loading IO event cache from the database")

    db_iface.set_mono_time_for_msg(mono_time)

    try:
        utils.proc_get_local(db_iface, proc_node, loc_name)
    except utils.NoMatchingLocalError:
        pass

    result = db_iface.query("START s=node(" + str(proc_node.id) + ") "
                            "MATCH (s)<-[:PROC_OBJ]-(l),"
                            "(l)-[?:IO_EVENTS]->(m), "
                            "p=(m)-[:PREV_EVENT*0..]->(n) "
                            "WHERE l.name = '" + loc_name + "' "
                            "AND not((n)-[:PREV_EVENT]->()) "
                            "RETURN l,NODES(p) ORDER BY l.mono_time")

    ret = common_utils.IndexList(lambda x: int(x.local['mono_time']))

    for row in result:
        chain = storage.FdChain()
        chain.local = row['l']
        if row['NODES(p)'] is not None:
            for node in row['NODES(p)']:
                chain.chain.insert(0, node)
        ret.append(chain)
    return ret


def process_aggregate_functions(db_iface, proc_node, msg_list):
    '''Processes an aggregation message.'''
    for smsg in msg_list:
        msg = uds_msg_pb2.FuncInfoMessage()
        msg.ParseFromString(smsg)
        des = get_fd_from_msg(msg)

        db_iface.set_mono_time_for_msg(msg.begin_time)

        idx_list = db_iface.cache_man.get(storage.CACHE_NAMES.IO_EVENT_CHAIN,
                                          (proc_node.id, des))

        if idx_list is None:
            idx_list = load_cache(db_iface, des, proc_node, msg.begin_time)

        evt = utils.event_from_msg(db_iface, msg)

        j = idx_list.find(evt, key=lambda x: int(x['before_time']))

        if j == 0:
            logging.error("Misplaced message.")
            logging.error(evt.__repr__())
            logging.error(idx_list)
            logging.error(evt['before_time'])
            continue

        # J pointed to local after the needed one
        chain = idx_list[j-1]

        if len(chain.chain) == 0:
            db_iface.create_relationship(chain.local, evt,
                                         storage.RelType.IO_EVENTS)
            db_iface.cache_man.invalidate(storage.CACHE_NAMES.LAST_EVENT,
                                          chain.local.id)
            chain.chain.insert(0, evt)
        else:
            i = chain.chain.find(evt)

            if i == 0:
                db_iface.create_relationship(
                    chain.chain[0], evt, storage.RelType.PREV_EVENT)
                chain.chain.insert(0, evt)
            elif i == len(chain.chain):
                end = len(chain.chain)

                for tmp_rel in chain.local.IO_EVENTS.outgoing:
                    db_iface.delete_relationship(tmp_rel)

                db_iface.create_relationship(
                    evt, chain.chain[end-1], storage.RelType.PREV_EVENT)
                db_iface.create_relationship(
                    chain.local, evt, storage.RelType.IO_EVENTS)

                db_iface.cache_man.invalidate(storage.CACHE_NAMES.LAST_EVENT,
                                              chain.local.id)
                chain.chain.append(evt)
            else:
                for tmp_rel in chain.chain[i].PREV_EVENT.outgoing:
                    db_iface.delete_relationship(tmp_rel)

                db_iface.create_relationship(
                    chain.chain[i], evt, storage.RelType.PREV_EVENT)
                db_iface.create_relationship(
                    evt, chain.chain[i-1], storage.RelType.PREV_EVENT)

                chain.chain.insert(i, evt)


@FuncController.dec('fork')
def posix_fork(db_iface, proc_node, msg):
    '''Implementation of fork in PVM semantics.'''
    process.ProcStateController.proc_fork(db_iface, proc_node,
                                          msg.ret_val, msg.begin_time)
    return proc_node


@FuncController.dec('popen')
@utils.check_message_error_num
def posix_popen(db_iface, proc_node, msg):
    '''Implementation of popen in PVM semantics.'''
    loc_node = pvm.get_l(db_iface, proc_node, str(msg.ret_val))
    return loc_node  # TODO(tb403) properly implement pipes


@FuncController.dec('fcloseall')
@utils.check_message_error_num
def posix_fcloseall(db_iface, proc_node, _):
    '''Implementation of fcloseall in PVM semantics.'''
    local_node_link_list = traversal.get_locals_from_process(db_iface,
                                                             proc_node)

    for (loc_node, _) in local_node_link_list:
        glob_node_link_list = traversal.get_globals_from_local(db_iface,
                                                               loc_node)

        for (glob_node, _) in glob_node_link_list:
            pvm.drop_g(db_iface, loc_node, glob_node)
        pvm.drop_l(db_iface, loc_node)

    return proc_node


@FuncController.dec('freopen')
def posix_freopen(db_iface, proc_node, msg):
    '''Implementation of freopen in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)

    try:
        utils.proc_get_local(db_iface, proc_node, args['stream'])
    except utils.NoMatchingLocalError:
        actions.close_action(msg.error_num, db_iface,
                             proc_node, args['stream'])

    git_hash = utils.parse_git_hash(msg)
    new_loc_node = actions.open_action(db_iface, proc_node,
                                       args['path'], str(msg.ret_val),
                                       git_hash)
    return new_loc_node


@FuncController.dec('freopen64')
def posix_freopen64(db_iface, proc_node, msg):
    '''Implementation of freopen64 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    try:
        utils.proc_get_local(db_iface, proc_node, args['stream'])
    except utils.NoMatchingLocalError:
        actions.close_action(msg.error_num, db_iface, proc_node,
                             args['stream'])

    git_hash = utils.parse_git_hash(msg)
    new_loc_node = actions.open_action(db_iface, proc_node,
                                       args['filename'], str(msg.ret_val),
                                       git_hash)
    return new_loc_node


@FuncController.dec('socket')
@utils.check_message_error_num
def posix_socket(db_iface, proc_node, msg):
    '''Implementation of socket in PVM semantics.'''
    loc_node = pvm.get_l(db_iface, proc_node, str(msg.ret_val))
    return loc_node


@FuncController.dec('accept')
@utils.check_message_error_num
def posix_accept(db_iface, proc_node, msg):
    '''Implementation of accept in PVM semantics.'''
    loc_node = pvm.get_l(db_iface, proc_node, str(msg.ret_val))
    return loc_node


@FuncController.dec('pipe')
@utils.check_message_error_num
def posix_pipe(db_iface, proc_node, msg):
    '''Implementation of pipe in PVM semantics.'''
    return utils.process_rw_pair(db_iface, proc_node, msg)


@FuncController.dec('pipe2')
@utils.check_message_error_num
def posix_pipe2(db_iface, proc_node, msg):
    '''Implementation of pipe2 in PVM semantics.'''
    return utils.process_rw_pair(db_iface, proc_node, msg)


@FuncController.dec('socketpair')
@utils.check_message_error_num
def posix_socketpair(db_iface, proc_node, msg):
    '''Implementation of socketpair in PVM semantics.'''
    return utils.process_rw_pair(db_iface, proc_node, msg)


@FuncController.dec('dup')
@utils.check_message_error_num
def posix_dup(db_iface, proc_node, msg):
    '''Implementation of dup in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    old_fd = args['oldfd']
    new_fd = str(msg.ret_val)
    loc_node = utils.proc_get_local(db_iface, proc_node, old_fd)
    utils.proc_dup_fd(db_iface, proc_node, old_fd, new_fd)
    return loc_node


@FuncController.dec('dup2')
@utils.check_message_error_num
def posix_dup2(db_iface, proc_node, msg):
    '''Implementation of dup2 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    old_fd = args['oldfd']
    new_fd = args['newfd']
    loc_node = utils.proc_get_local(db_iface, proc_node, old_fd)
    utils.proc_dup_fd(db_iface, proc_node, old_fd, new_fd)
    return loc_node


@FuncController.dec('dup3')
@utils.check_message_error_num
def posix_dup3(db_iface, proc_node, msg):
    '''Implementation of dup3 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    old_fd = args['oldfd']
    new_fd = args['newfd']
    loc_node = utils.proc_get_local(db_iface, proc_node, old_fd)
    utils.proc_dup_fd(db_iface, proc_node, old_fd, new_fd)
    return loc_node


@FuncController.dec('renameat')
def posix_renameat(db_iface, p_id, msg):
    '''Implementation of renameat in PVM semantics.'''
    return posix_rename(db_iface, p_id, msg)


@FuncController.dec('rename')
def posix_rename(db_iface, proc_node, msg):
    '''Implementation of rename in PVM semantics.'''
    # TODO(tb403): Fix to only use a single omega.
    args = utils.parse_kvpair_list(msg.args)
    dest_glob_node = traversal.get_latest_glob_version(db_iface,
                                                       args['newpath'])

    if dest_glob_node is not None:
        if traversal.is_glob_deleted(dest_glob_node) is False:
            actions.delete_action(db_iface, proc_node, args['newpath'])
    loc_node = actions.link_action(db_iface, proc_node,
                                   args['oldpath'], args['newpath'])
    actions.delete_action(db_iface, proc_node, args['oldpath'])
    return loc_node


@FuncController.dec('umask')
def posix_umask(db_iface, proc_node, msg):
    '''Implementation of umask in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "file_mode_creation_mask",
                           args["mask"], msg.end_time)
    return proc_node


@FuncController.dec('tmpfile')
@utils.check_message_error_num
def posix_tmpfile(db_iface, proc_node, msg):
    '''Implementation of tmpfile in PVM semantics.'''
    loc_node = pvm.get_l(db_iface, proc_node, str(msg.ret_val))
    return loc_node


@FuncController.dec('tmpfile64')
@utils.check_message_error_num
def posix_tmpfile64(db_iface, proc_node, msg):
    '''Implementation of tmpfile64 in PVM semantics.'''
    loc_node = pvm.get_l(db_iface, proc_node, str(msg.ret_val))
    return loc_node


@FuncController.dec('chdir')
@utils.check_message_error_num
def posix_chdir(db_iface, proc_node, msg):
    '''Implementation of chdir in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "cwd",
                           args["path"], msg.end_time)
    return proc_node


@FuncController.dec('fchdir')
def posix_fchdir(db_iface, proc_node, msg):
    '''Implementation of fchdir in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    try:
        loc_node = utils.proc_get_local(db_iface, proc_node, args['fd'])
    except utils.NoMatchingLocalError:
        return proc_node

    if msg.error_num > 0:
        return loc_node

    glob_node_rel_list = traversal.get_globals_from_local(db_iface, loc_node)
    if len(glob_node_rel_list) == 0 or len(glob_node_rel_list) > 1:
        return loc_node

    glob_node, _ = glob_node_rel_list[0]
    name_list = glob_node['name']
    dir_name = name_list[0]

    utils.update_proc_meta(db_iface, proc_node, "cwd",
                           dir_name, msg.end_time)
    return loc_node


@FuncController.dec('seteuid')
@utils.check_message_error_num
def posix_seteuid(db_iface, proc_node, msg):
    '''Implementation of seteuid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "euid",
                           args["euid"], msg.end_time)
    return proc_node


@FuncController.dec('setegid')
@utils.check_message_error_num
def posix_setegid(db_iface, proc_node, msg):
    '''Implementation of setegid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "egid",
                           args["egid"], msg.end_time)
    return proc_node


@FuncController.dec('setgid')
@utils.check_message_error_num
def posix_setgid(db_iface, proc_node, msg):
    '''Implementation of setgid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "gid",
                           args["gid"], msg.end_time)
    return proc_node


@FuncController.dec('setreuid')
@utils.check_message_error_num
def posix_setreuid(db_iface, proc_node, msg):
    '''Implementation of setreuid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "ruid",
                           args["ruid"], msg.end_time)
    utils.update_proc_meta(db_iface, proc_node, "euid",
                           args["euid"], msg.end_time)
    return proc_node


@FuncController.dec('setregid')
@utils.check_message_error_num
def posix_setregid(db_iface, proc_node, msg):
    '''Implementation of setregid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "rgid",
                           args["rgid"], msg.end_time)
    utils.update_proc_meta(db_iface, proc_node, "egid",
                           args["egid"], msg.end_time)
    return proc_node


@FuncController.dec('setuid')
@utils.check_message_error_num
def posix_setuid(db_iface, proc_node, msg):
    '''Implementation of setuid in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    utils.update_proc_meta(db_iface, proc_node, "uid",
                           args["uid"], msg.end_time)
    return proc_node


@FuncController.dec('clearenv')
@utils.check_message_error_num
def posix_clearenv(db_iface, proc_node, msg):
    '''Implementation of clearenv in PVM semantics.'''
    env_meta_list = traversal.get_proc_meta(db_iface, proc_node,
                                            storage.RelType.ENV_META)

    for meta_node, meta_rel in env_meta_list:
        utils.version_meta(db_iface, proc_node, meta_node, meta_rel,
                           (meta_node['name'], None, msg.end_time))
    return proc_node


@FuncController.dec('putenv')
@utils.check_message_error_num
def posix_putenv(db_iface, proc_node, msg):
    '''Implementation of putenv in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)

    parts = args['string'].split("=")
    if len(parts) == 2:
        env = (parts[0], parts[1], msg.end_time)
    else:
        env = (parts[0], None, msg.end_time)
    utils.process_put_env(db_iface, proc_node, env, True)
    return proc_node


@FuncController.dec('setenv')
@utils.check_message_error_num
def posix_setenv(db_iface, proc_node, msg):
    '''Implementation of setenv in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    env = (args['name'], args['value'], msg.end_time)
    utils.process_put_env(db_iface, proc_node, env, args['overwrite'] > 0)
    return proc_node


@FuncController.dec('unsetenv')
@utils.check_message_error_num
def posix_unsetenv(db_iface, proc_node, msg):
    '''Implementation of unsetenv in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    env = (args['name'], None, msg.end_time)
    utils.process_put_env(db_iface, proc_node, env, True)
    return proc_node


@FuncController.dec('fcntl')
@utils.check_message_error_num
def posix_fcntl(db_iface, proc_node, msg):
    '''Implementation of fnctl in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_node = utils.proc_get_local(db_iface, proc_node, args['filedes'])

    if int(args['cmd']) == fcntl.F_DUPFD:
        utils.proc_dup_fd(db_iface, proc_node,
                          args['filedes'], str(msg.ret_val))
    if int(args['cmd']) == common_utils.FCNTL_F_DUPFD_CLOEXEC:
        utils.proc_dup_fd(db_iface, proc_node,
                          args['filedes'], str(msg.ret_val),
                          storage.LinkState.CLOEXEC)
    if int(args['cmd']) == fcntl.F_SETFD:
        if int(args['arg']) == fcntl.FD_CLOEXEC:
            db_iface.set_link_state(loc_node.PROC_OBJ.outgoing,
                                    storage.LinkState.CLOEXEC)
        else:
            db_iface.set_link_state(loc_node.PROC_OBJ.outgoing,
                                    storage.LinkState.NONE)

    return loc_node

#
# OPEN FUNCTIONS
#


def parse_fmode(mode):
    '''Parses a f* function type mode into a linkstate.'''
    if "r+" in mode or "w+" in mode or "a+" in mode:
        return storage.LinkState.RaW
    elif "w" in mode or "a" in mode:
        return storage.LinkState.WRITE
    else:
        return storage.LinkState.READ


def parse_omode(mode):
    '''Parses an open type mode into a linkstate.'''
    if int(mode) & os.O_RDWR != 0:
        return storage.LinkState.RaW
    elif int(mode) & os.O_WRONLY != 0:
        return storage.LinkState.WRITE
    else:
        return storage.LinkState.READ


@FuncController.dec('creat')
@utils.check_message_error_num
def posix_creat(db_iface, proc_node, msg):
    '''Implementation of creat in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['pathname'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = storage.LinkState.WRITE
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('creat64')
@utils.check_message_error_num
def posix_creat64(db_iface, proc_node, msg):
    '''Implementation of creat64 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['pathname'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = storage.LinkState.WRITE
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('fopen')
@utils.check_message_error_num
def posix_fopen(db_iface, proc_node, msg):
    '''Implementation of fopen in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    git_hash = utils.parse_git_hash(msg)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['path'], str(msg.ret_val),
                                  git_hash)
    if proc_node['opus_lite']:
        state = parse_fmode(args['mode'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('fopen64')
@utils.check_message_error_num
def posix_fopen64(db_iface, proc_node, msg):
    '''Implementation of fopen64 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    git_hash = utils.parse_git_hash(msg)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['path'], str(msg.ret_val),
                                  git_hash)
    if proc_node['opus_lite']:
        state = parse_fmode(args['mode'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('open')
@utils.check_message_error_num
def posix_open(db_iface, proc_node, msg):
    '''Implementation of open in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    git_hash = utils.parse_git_hash(msg)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['pathname'], str(msg.ret_val),
                                  git_hash)
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('open64')
@utils.check_message_error_num
def posix_open64(db_iface, proc_node, msg):
    '''Implementation of open64 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    git_hash = utils.parse_git_hash(msg)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['pathname'], str(msg.ret_val),
                                  git_hash)
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('mkstemp')
@utils.check_message_error_num
def posix_mkstemp(db_iface, proc_node, msg):
    '''Implementation of mkstemp in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['templ'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = storage.LinkState.RaW
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('mkostemp')
@utils.check_message_error_num
def posix_mkostemp(db_iface, proc_node, msg):
    '''Implementation of mkostemp in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['templ'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('mkstemps')
@utils.check_message_error_num
def posix_mkstemps(db_iface, proc_node, msg):
    '''Implementation of mkstemps in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['templ'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = storage.LinkState.RaW
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('mkostemps')
@utils.check_message_error_num
def posix_mkostemps(db_iface, proc_node, msg):
    '''Implementation of mkostemps in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['templ'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('openat')
@utils.check_message_error_num
def posix_openat(db_iface, proc_node, msg):
    '''Implementation of openat in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['file_path'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj


@FuncController.dec('openat64')
@utils.check_message_error_num
def posix_openat64(db_iface, proc_node, msg):
    '''Implementation of openat64 in PVM semantics.'''
    args = utils.parse_kvpair_list(msg.args)
    loc_obj = actions.open_action(db_iface, proc_node,
                                  args['file_path'], str(msg.ret_val))
    if proc_node['opus_lite']:
        state = parse_omode(args['flags'])
        for rel in loc_obj.LOC_OBJ.incoming:
            rel['state'] = state
    return loc_obj

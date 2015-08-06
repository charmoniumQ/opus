#! /usr/bin/env python2.7
# -*- coding: utf-8 -*-
"""

"""
from __future__ import (absolute_import, division,
                        print_function)


import argparse
import functools
import hashlib
import os
import os.path
import sys
import time

OPUS_AVAILABLE = True
try:
    from opus import cc_utils, exception
except ImportError as exc:
    OPUS_AVAILABLE = False

import prettytable
import psutil
import yaml
from termcolor import colored


class OPUSctlError(Exception):
    pass


class FailedConfigError(OPUSctlError):
    pass


def memoised(func):
    cache = func.cache = {}

    @functools.wraps(func)
    def inner(*args, **kwargs):
        key = str(args) + str(kwargs)
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]
    return inner


@memoised
def path_normalise(path):
    return os.path.abspath(os.path.expanduser(path))


PS_SYMBOL = u"●"


DEFAULT_CONFIG_PATH = "~/.opus-cfg"

if 'OPUS_MASTER_CONFIG' in os.environ:
    CONFIG_PATH = path_normalise(os.environ['OPUS_MASTER_CONFIG'])
else:
    CONFIG_PATH = path_normalise(DEFAULT_CONFIG_PATH)

CONFIG_SETUP = [
    {'key': 'master_config',
     'def': lambda _: DEFAULT_CONFIG_PATH,
     'prompt': 'Choose a location for the OPUS master config'},

    {'key': 'install_dir',
     'def': lambda _: '~/.opus',
     'prompt': 'Choose a directory for your OPUS installation to reside in'},

    {'key': 'uds_path',
     'def': lambda cfg: os.path.join(cfg['install_dir'], 'uds_sock'),
     'prompt': 'Choose a location for the OPUS Unix Domain Socket'},

    {'key': 'db_path',
     'def': lambda cfg: os.path.join(cfg['install_dir'], 'prov.neo4j'),
     'prompt': 'Choose a location for the OPUS database to reside in'},

    {'key': 'bash_var_path',
     'def': lambda _: '~/.opus-vars',
     'prompt': 'Choose a location for the OPUS bash variables cfg_file'},

    {'key': 'python_binary',
     'def': lambda _: '/usr/bin/python2.7',
     'prompt': 'What is the location of your python 2.7 binary'},

    {'key': 'java_home',
     'def': lambda _: '/usr/lib/jvm/java-7-common',
     'prompt': 'Where is your jvm installation'},

    {'key': 'cc_port',
     'def': lambda _: '10101',
     'prompt': 'Port to use for provenance server communications.'},

    {'key': 'debug_mode',
     'def': lambda _: False,
     'prompt': 'Set OPUS to debug mode'}
]


BASH_VAR_TEMPLATE = """\
#Auto generated by opusctl
export PATH=$PATH:{bin_dir}
export PYTHONPATH=$PYTHONPATH:{lib_dir}:{py_dir}
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=cpp
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION_VERSION=2
export OPUS_MASTER_CONFIG={conf_loc}
"""


BACKEND_CONFIG_TEMPLATE = """\
LOGGING:
  version: 1
  formatters:
    full:
      format: \"%(asctime)s %(levelname)s L%(lineno)d -> %(message)s\"
  handlers:
    file:
      class: logging.FileHandler
      level: DEBUG
      formatter: full
      filename: {log_file}
  root:
    level: {log_level}
    handlers: [file]

MODULES:
  Producer: SocketProducer
  Analyser: PVMAnalyser
  CommandInterface: TCPInterface

PRODUCER:
  SocketProducer:
    comm_mgr_type: UDSCommunicationManager
    comm_mgr_args:
        uds_path: {uds_sock}
        max_conn: 10
        select_timeout: 5.0

ANALYSER:
  PVMAnalyser:
    storage_type: DBInterface
    storage_args:
      filename: {db_path}
    opus_lite: true

COMMANDINTERFACE:
  TCPInterface:
    listen_addr: localhost
    listen_port: {cc_port}

GENERAL:
  touch_file: {touch_file}
"""


def auto_read_config(func):
    @functools.wraps(func)
    def inner(config, *args, **kwargs):
        return func(cfg=load_config(config),
                    *args,
                    **kwargs)
    return inner


@memoised
def compute_config_check(cfg):
    sha1 = hashlib.sha1()

    cfg_str = yaml.dump(cfg)
    sha1.update(cfg_str)
    return cfg_str, sha1.hexdigest()


def is_opus_active():
    return ("LD_PRELOAD" in os.environ and
            "libopusinterpose.so" in os.environ['LD_PRELOAD'] and
            ("OPUS_INTERPOSE_MODE" in os.environ and
             os.environ['OPUS_INTERPOSE_MODE'] != "0"))


def read_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as cfg_file:
                check = cfg_file.readline().rstrip()
                return (check, yaml.load(cfg_file.read()))
        except [yaml.error.YAMLError, IOError]:
            raise FailedConfigError()
    else:
        raise FailedConfigError()


def write_config(config_path, cfg):
    config_path = path_normalise(config_path)
    cfg_str, cfg_check = compute_config_check(cfg)
    with open(config_path, "w") as cfg_file:
        cfg_file.write(cfg_check)
        cfg_file.write("\n")
        cfg_file.write(cfg_str)


def load_config(config_path=CONFIG_PATH):
    try:
        check, cfg = read_config(config_path)
    except FailedConfigError:
        print("Error: Your config file is missing.")
        resp = raw_input("Do you want to regenerate it? [Y/n]")
        if resp == "" or resp.upper() == "Y":
            cfg = generate_config()
            check = ""
        else:
            raise

    _, cfg_check = compute_config_check(cfg)
    if check != cfg_check:
        update_config_subsidiaries(cfg)
        write_config(config_path, cfg)
    return cfg


def update_config_subsidiaries(cfg):
    print("Config file modified, applying...")
    generate_bash_var_file(cfg)
    generate_backend_cfg_file(cfg)
    print("Application complete.")


def generate_config(existing=None):
    if existing is None:
        existing = {}
    cfg = {}

    for quest in CONFIG_SETUP:
        if quest['key'] in existing:
            default = existing[quest['key']]
        else:
            default = quest['def'](cfg)
        prompt = "{} [{}]:".format(quest['prompt'], default)
        resp = raw_input(prompt)
        if resp == "":
            cfg[quest['key']] = default
        else:
            cfg[quest['key']] = resp

    return cfg


def generate_bash_var_file(cfg):
    var_file_path = path_normalise(cfg['bash_var_path'])

    with open(var_file_path, "w") as var_file:
        var_file.write(
            BASH_VAR_TEMPLATE.format(
                bin_dir=path_normalise(
                    os.path.join(cfg['install_dir'], "bin")
                ),
                lib_dir=path_normalise(
                    os.path.join(cfg['install_dir'], "lib")
                ),
                py_dir=path_normalise(
                    os.path.join(cfg['install_dir'], "src", "backend")
                ),
                conf_loc=path_normalise(
                    cfg['master_config']
                )
            )
        )


def generate_backend_cfg_file(cfg):
    backend_cfg_path = path_normalise(os.path.join(cfg['install_dir'],
                                                   "opus-cfg.yaml"))

    log_level = "DEBUG" if cfg['debug_mode'] else "ERROR"
    log_file = path_normalise(os.path.join(cfg['install_dir'], "opus.log"))
    uds_sock = path_normalise(cfg['uds_path'])
    db_path = path_normalise(cfg['db_path'])
    touch_file = path_normalise(os.path.join(cfg['install_dir'], ".opus-live"))
    cc_port = cfg['cc_port']

    with open(backend_cfg_path, "w") as backend_cfg:
        backend_cfg.write(
            BACKEND_CONFIG_TEMPLATE.format(
                log_level=log_level,
                log_file=log_file,
                uds_sock=uds_sock,
                db_path=db_path,
                touch_file=touch_file,
                cc_port=cc_port))


def is_backend_active(cfg):
    opus_pid_file = path_normalise(os.path.join(cfg['install_dir'], ".pid"))
    try:
        with open(opus_pid_file, "r") as p_file:
            opus_pid = int(p_file.read())
    except IOError:
        return False

    try:
        opus = psutil.Process(opus_pid)
    except psutil.NoSuchProcess:
        return False

    cmd_str = ' '.join(opus.cmdline())
    return "run_server.py" in cmd_str


def elapsed(reset=False):
    if reset:
        elapsed.start = time.time()
    else:
        return time.time() - elapsed.start


def monitor_backend_startup(cfg):
    if not OPUS_AVAILABLE:
        print("OPUS libraries not available in $PYTHONPATH, "
              "please check your environment and try again.")
        print("Unable to check OPUS backend startup status.")
        print("Assuming backend has started.")
        return True
    elapsed(reset=True)
    time.sleep(0.1)
    while elapsed() < 20:
        backend_active = is_backend_active(cfg)
        try:
            helper = cc_utils.CommandConnectionHelper("localhost",
                                                      int(cfg['cc_port']))
            helper.make_request({'cmd': 'status'})
            backend_responsive = True
        except exception.BackendConnectionError:
            backend_responsive = False

        yes = colored("yes", "green")
        no = colored("no", "red")

        print("\rServer Active: %s  Server Responsive: %s" %
              ((yes if backend_active else no),
               (yes if backend_responsive else no)),
              end="")
        if not(backend_active or backend_responsive):
            break

        if backend_active and backend_responsive:
            print("\nBackend sucessfully started.")
            return True
        time.sleep(0.1)
    print("\nBackend startup failed, check the %s and %s error logs for "
          "information." % (os.path.join(cfg['install_dir'], "opus_err.log"),
                            os.path.join(cfg['install_dir'], "opus.log")))
    return False


def start_opus_backend(cfg):
    print("Attempting to start OPUS backend.")
    if is_opus_active():
        os.environ['OPUS_INTERPOSE_MODE'] = "0"
    if 'JAVA_HOME' not in os.environ:
        os.environ['JAVA_HOME'] = cfg['java_home']

    try:
        pid = os.fork()
        if pid > 0:
            return monitor_backend_startup(cfg)
    except OSError:
        sys.exit(1)

    os.chdir(path_normalise(cfg['install_dir']))
    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    err_log = path_normalise(os.path.join(cfg['install_dir'], "opus_err.log"))
    sys.stdout.flush()
    sys.stderr.flush()
    sti = file("/dev/null", 'r')
    sto = open(err_log, 'w+')
    os.dup2(sti.fileno(), sys.stdin.fileno())
    os.dup2(sto.fileno(), sys.stdout.fileno())
    os.dup2(sto.fileno(), sys.stderr.fileno())
    sto.close()

    opus_pid_file = path_normalise(os.path.join(cfg['install_dir'],
                                                ".pid"))

    backend_cfg_path = path_normalise(os.path.join(cfg['install_dir'],
                                                   "opus-cfg.yaml"))
    run_server_path = path_normalise(os.path.join(cfg['install_dir'],
                                                  "src", "backend",
                                                  "run_server.py"))

    try:
        pid = os.fork()
        if pid > 0:
            os.waitpid(pid, 0)
        else:
            pid = str(os.getpid())
            p_file = open(opus_pid_file, 'w+')
            p_file.write(pid)
            p_file.close()
            os.execvp(cfg['python_binary'],
                      [cfg['python_binary'],
                       "-O",
                       run_server_path,
                       backend_cfg_path])
    except OSError:
        sys.exit(1)

    os.unlink(opus_pid_file)


@auto_read_config
def handle_launch(cfg, binary, arguments):
    if not is_backend_active(cfg):
        if not start_opus_backend(cfg):
            print("Aborting command launch.")
            return

    opus_preload_lib = path_normalise(os.path.join(cfg['install_dir'],
                                                   'lib',
                                                   'libopusinterpose.so'))
    if 'LD_PRELOAD' in os.environ:
        if opus_preload_lib not in os.environ['LD_PRELOAD']:
            os.environ['LD_PRELOAD'] = (os.environ['LD_PRELOAD'] + " " +
                                        opus_preload_lib)
    else:
        os.environ['LD_PRELOAD'] = opus_preload_lib

    os.environ['OPUS_UDS_PATH'] = path_normalise(cfg['uds_path'])
    os.environ['OPUS_MSG_AGGR'] = "1"
    os.environ['OPUS_MAX_AGGR_MSG_SIZE'] = "65536"
    os.environ['OPUS_LOG_LEVEL'] = "3"  # Log critical
    os.environ['OPUS_INTERPOSE_MODE'] = "1"  # OPUS lite

    os.execvp(binary, [binary] + arguments)


@auto_read_config
def handle_exclude(cfg, binary, arguments):
    if is_opus_active():
        del os.environ['OPUS_INTERPOSE_MODE']
        del os.environ['OPUS_UDS_PATH']
        del os.environ['OPUS_MSG_AGGR']
        del os.environ['OPUS_MAX_AGGR_MSG_SIZE']
        del os.environ['OPUS_LOG_LEVEL']
        opus_preload_lib = path_normalise(os.path.join(cfg['install_dir'],
                                                       'lib',
                                                       'libopusinterpose.so'))
        if os.environ['LD_PRELOAD'] == opus_preload_lib:
            del os.environ['LD_PRELOAD']
        else:
            os.environ['LD_PRELOAD'] = os.environ['LD_PRELOAD'].replace(
                opus_preload_lib, ""
            ).strip()
    else:
        print("OPUS is not active.")
    os.execvp(binary, [binary] + arguments)


def handle_start(cfg):
    '''Starts OPUS backend.'''
    if not is_backend_active(cfg):
        start_opus_backend(cfg)
    else:
        print("OPUS backend already running.")


def handle_process(cmd, **params):
    if cmd == "launch":
        handle_launch(**params)
    elif cmd == "exclude":
        handle_exclude(**params)


@auto_read_config
def handle_server(cfg, cmd, **params):
    if cmd == "start":
        handle_start(cfg=cfg, **params)
    else:
        if not is_backend_active(cfg):
            print("Server is not running.")
            return

        if not OPUS_AVAILABLE:
            print("OPUS libraries not available in $PYTHONPATH, "
                  "please check your environment and try again.")
            return
        helper = cc_utils.CommandConnectionHelper("localhost",
                                                  int(cfg['cc_port']))

        msg = {"cmd": cmd}
        msg.update(params)
        pay = helper.make_request(msg)

        if cmd == "stop":
            if pay['success']:
                print("Server stopped successfully.")
            else:
                print("Server stop failed.")
        elif not pay['success']:
            print(pay['msg'])
        elif cmd == "ps":
            tab = prettytable.PrettyTable(['Pid', 'Thread Count'])
            print("Interposed Processes:\n\n")
            for pid, count in pay['pid_map'].items():
                tab.add_row([pid, count])
            print(tab)
        elif cmd == "status":
            print_status_rsp(pay)
        else:
            print(pay['msg'])


def handle_conf(config, install):
    try:
        _, cfg = read_config(config)
    except FailedConfigError:
        cfg = {}

    if config is not None:
        cfg['master_config'] = config

    new_cfg = generate_config(cfg)

    update_config_subsidiaries(new_cfg)

    write_config(new_cfg['master_config'], new_cfg)

    if install:
        with open("/tmp/install-opus", "w") as handle:
            handle.write("source " + new_cfg['bash_var_path'])


def handle_util(cmd, **params):
    if cmd == "ps-line":
        handle_ps_line(**params)


@auto_read_config
def handle_ps_line(cfg):
    term_status = is_opus_active()
    server_status = is_backend_active(cfg)
    if server_status:
        if term_status:
            color = "green"
        else:
            color = "yellow"
    else:
        color = "red"
    print(colored(PS_SYMBOL, color).encode("utf-8"), end="")


def print_status_rsp(pay):
    '''Prints status response to stdout'''

    print("{0:<20} {1:<12}".format("Producer", pay['producer']['status']))

    if 'num_msgs' in pay['analyser']:
        print("{0:<20} {1:<12} {2:<20}".format(
            "Analyser",
            pay['analyser']['status'],
            "(" + str(pay['analyser']['num_msgs']) + " msgs in queue)"))
    else:
        print("{0:<20} {1:<12}".format("Analyser", pay['analyser']['status']))

    print("{0:<20} {1:<12}".format("Query Interface", pay['query']['status']))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=False, default=CONFIG_PATH,
                        help="Path to OPUS master config file.")

    group_parser = parser.add_subparsers(dest="group")
    proc_parser = group_parser.add_parser(
        "process",
        help="Commands for launching processes with or without OPUS "
             "interposition.")
    server_parser = group_parser.add_parser(
        "server",
        help="Commands for controlling the provenance collection server.")
    conf_parser = group_parser.add_parser(
        "conf",
        help="Configuration of the OPUS environment.")
    util_parser = group_parser.add_parser(
        "util",
        help="Utility functions for OPUS.")

    proc_cmds = proc_parser.add_subparsers(dest="cmd")

    launch = proc_cmds.add_parser(
        "launch",
        help="Launch a process under OPUS.")
    launch.add_argument(
        "binary", nargs='?', default=os.environ['SHELL'],
        help="The binary to be launched. Defaults to the current shell.")
    launch.add_argument(
        "arguments", nargs=argparse.REMAINDER,
        help="Any arguments to be passed.")

    exclude = proc_cmds.add_parser(
        "exclude",
        help="Launch a process excluded from OPUS interposition.")
    exclude.add_argument(
        "binary", nargs='?', default=os.environ['SHELL'],
        help="The binary to be launched. Defaults to the current shell.")
    exclude.add_argument(
        "arguments", nargs=argparse.REMAINDER,
        help="Any arguments to be passed.")

    server_cmds = server_parser.add_subparsers(dest="cmd")
    server_cmds.add_parser(
        "start",
        help="Start the OPUS provenance collection server.")
    server_cmds.add_parser(
        "stop",
        help="Stop the OPUS provenance collection server.")
    server_cmds.add_parser(
        "ps",
        help="Display a list of processes currently being interposed.")
    server_cmds.add_parser(
        "status",
        help="Display a status readout for the provenance collection server.")

    detach_parser = server_cmds.add_parser(
        "detach",
        help="Deactivates OPUS interposition on a running process.")
    detach_parser.add_argument(
        "pid", type=int,
        help="The PID requiring interposition deactivation.")

    server_cmds.add_parser("getan")

    setan_parser = server_cmds.add_parser("setan")
    setan_parser.add_argument("new_an")

    conf_parser.add_argument(
        "--install", "-i", action='store_true',
        help="Triggers additional output during the install procedure.")

    util_cmds = util_parser.add_subparsers(dest="cmd")
    util_cmds.add_parser(
        "ps-line",
        help=(u"Provides a $PS line component for indicating the status "
              u"of OPUS. "
              u"{} : Server running and current session interposed. "
              u"{} : Server running but no session interposition. "
              u"{} : Server offline.").format(colored(PS_SYMBOL, "green"),
                                              colored(PS_SYMBOL, "yellow"),
                                              colored(PS_SYMBOL, "red")
                                              ).encode("utf-8"))

    return parser.parse_args()


def main():
    args = parse_args()

    params = {k: v
              for k, v in args._get_kwargs()  # pylint: disable=W0212
              if k != 'group'}

    try:
        if args.group == "process":
            handle_process(**params)
        elif args.group == "server":
            handle_server(**params)
        elif args.group == "conf":
            handle_conf(**params)
        elif args.group == "util":
            handle_util(**params)
    except FailedConfigError:
        print("Failed to execute command due to insufficient configuration. "
              "Please run the '{} conf' command "
              "to reconfigure the program.".format(sys.argv[0]))

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import os
import time
import sys

from . import utils
from .ext_deps import termcolor
from .. import cc_utils


def elapsed(reset=False):
    if reset:
        elapsed.start = time.time()
    else:
        return time.time() - elapsed.start


def monitor_server_startup(cfg):
    elapsed(reset=True)
    time.sleep(3)
    helper = cc_utils.CommandConnectionHelper(cfg['cc_addr'])
    server_active = False
    while elapsed() < 20:

        yes = termcolor.colored("yes", "green")
        no = termcolor.colored("no", "red")

        print((" " * 50), end="\r")
        print("Server Active: %s" %
              (yes if server_active else no),
              end="\r")
        sys.stdout.flush()

        if server_active:
            print("\nServer sucessfully started.")
            return True

        server_active = utils.is_server_active(helper=helper)
        time.sleep(0.1)
    print("\nServer startup failed, check the %s and %s error logs for "
          "information." % (os.path.join(cfg['data_dir'], "opus_err.log"),
                            os.path.join(cfg['data_dir'], "opus.log")))
    return False


def start_opus_server(cfg):
    print("Attempting to start OPUS server.")
    if utils.is_opus_active() or utils.is_opus_ipose_lib_set():
        utils.reset_opus_env(cfg)
    if 'JAVA_HOME' not in os.environ:
        os.environ['JAVA_HOME'] = cfg['java_home']

    try:
        pid = os.fork()
        if pid > 0:
            return monitor_server_startup(cfg)
    except OSError:
        sys.exit(1)

    os.chdir(utils.path_normalise(cfg['data_dir']))
    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    err_log = utils.path_normalise(os.path.join(cfg['data_dir'],
                                                "opus_err.log"))
    sys.stdout.flush()
    sys.stderr.flush()
    sti = open("/dev/null", 'r')
    sto = open(err_log, 'w+')
    os.dup2(sti.fileno(), sys.stdin.fileno())
    os.dup2(sto.fileno(), sys.stdout.fileno())
    os.dup2(sto.fileno(), sys.stderr.fileno())
    sto.close()

    server_cfg_path = utils.path_normalise(os.path.join(cfg['data_dir'],
                                                        "opus-cfg.yaml"))

    try:
        os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'cpp'
        os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION_VERSION'] = '2'
        python_root = (os.path.dirname(os.path.dirname(sys.executable)))
        os.environ["PYTHONPATH"] = ":".join(filter(lambda path: path and not path.startswith(python_root), sys.path))
        debug_args = [] if cfg["debug_mode"] else ["-O"]
        os.execvp(sys.executable,
                  [sys.executable] + debug_args + [
                   "-m",
                   "opus.run_server",
                   server_cfg_path])
    except OSError:
        sys.exit(1)

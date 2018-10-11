# Copyright 2018 Francis Y. Yan, Jestin Ma
# Copyright 2018 Wei Wang, Yiyang Shao (Huawei Technologies)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.


import os
from os import path
import time
import errno
import select
import socket
import operator
import numpy as np
import ConfigParser
import ast

import context

READ_FLAGS = select.POLLIN | select.POLLPRI
WRITE_FLAGS = select.POLLOUT
ERR_FLAGS = select.POLLERR | select.POLLHUP | select.POLLNVAL
READ_ERR_FLAGS = READ_FLAGS | ERR_FLAGS
ALL_FLAGS = READ_FLAGS | WRITE_FLAGS | ERR_FLAGS


def format_actions(action_list):
    ret = []

    for action in action_list:
        op = action[0]
        val = float(action[1:])

        if op == '+':
            ret.append((operator.add, val))
        elif op == '-':
            ret.append((operator.sub, val))
        elif op == '*':
            ret.append((operator.mul, val))
        elif op == '/':
            ret.append((operator.div, val))

    return ret


def timestamp_ms():
    return int(round(time.time() * 1000))


def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def get_open_port():
    sock = socket.socket(socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def check_pid(pid):
    """ Check for the existence of a unix pid """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def one_hot(action, action_cnt):
    ret = [0.0] * action_cnt
    ret[action] = 1.0
    return ret


def softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum(axis=0)


def update_ewma(ewma, new_value):
    if ewma is None:
        return float(new_value)
    else:
        return 0.875 * ewma + 0.125 * new_value


class RingBuffer(object):
    def __init__(self, length):
        self.full_len = length
        self.real_len = 0
        self.index = 0
        self.data = np.zeros(length)

    def append(self, x):
        self.data[self.index] = x
        self.index = (self.index + 1) % self.full_len
        if self.real_len < self.full_len:
            self.real_len += 1

    def get(self):
        idx = (self.index - self.real_len +
               np.arange(self.real_len)) % self.full_len
        return self.data[idx]

    def reset(self):
        self.real_len = 0
        self.index = 0
        self.data.fill(0)


class MeanVarHistory(object):
    def __init__(self):
        self.length = 0
        self.mean = 0.0
        self.square_mean = 0.0
        self.var = 0.0

    def append(self, x):
        """Append x to history.

        Args:
            x: a list or numpy array.
        """
        # x: a list or numpy array
        length_new = self.length + len(x)
        ratio_old = float(self.length) / length_new
        ratio_new = float(len(x)) / length_new

        self.length = length_new
        self.mean = self.mean * ratio_old + np.mean(x) * ratio_new
        self.square_mean = (self.square_mean * ratio_old +
                            np.mean(np.square(x)) * ratio_new)
        self.var = self.square_mean - np.square(self.mean)

    def get_mean(self):
        return self.mean

    def get_var(self):
        return self.var if self.var > 0 else 1e-10

    def get_std(self):
        return np.sqrt(self.get_var())

    def normalize_copy(self, x):
        """Normalize x and returns a copy.

        Args:
            x: a list or numpy array.
        """
        return [(v - self.mean) / self.get_std() for v in x]

    def normalize_inplace(self, x):
        """Normalize x in place.

        Args:
            x: a numpy array with float dtype.
        """
        x -= self.mean
        x /= self.get_std()

    def reset(self):
        self.length = 0
        self.mean = 0.0
        self.square_mean = 0.0
        self.var = 0.0


def ssh_cmd(host):
    return ['ssh', '-q', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=5', host]


class Config(object):
    cfg = ConfigParser.ConfigParser()
    cfg_path = path.join(context.base_dir, 'config.ini')
    cfg.read(cfg_path)

    state_dim = int(cfg.get('global', 'state_dim'))
    state_idx = int(cfg.get('global', 'state'))
    fri = float(cfg.get('global', 'fri'))
    rho = float(cfg.get('global', 'rho'))

    total_tp_set_train = []
    total_env_set_train = []
    train_env = cfg.options('train_env')
    for opt in train_env:
        env_param, tp_set_param = ast.literal_eval(cfg.get('train_env', opt))
        total_tp_set_train.append(
                ast.literal_eval(cfg.get('global', tp_set_param)))
        total_env_set_train.append(
                ast.literal_eval(cfg.get('global', env_param)))

    total_tp_set_test = []
    total_env_set_test = []
    test_env = cfg.options('test_env')
    for opt in test_env:
        env_param, tp_set_param = ast.literal_eval(cfg.get('test_env', opt))
        total_tp_set_test.append(
                ast.literal_eval(cfg.get('global', tp_set_param)))
        total_env_set_test.append(
                ast.literal_eval(cfg.get('global', env_param)))

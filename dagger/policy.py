# Copyright 2018 Francis Y. Yan
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


import sys

from message import Message
import context
from helpers.utils import timestamp_ms, update_ewma, format_actions


class Policy(object):
    min_cwnd = 10.0
    max_cwnd = 25000.0

    max_rtt = 300.0  # ms
    max_delay = max_rtt
    max_send_rate = 1000.0  # Mbps
    max_delivery_rate = max_send_rate

    min_step_len = 10  # ms
    steps_per_episode = 1000  # number of steps in each episode (in training)

    # state = [rtt_norm, delay_norm, send_rate_norm, delivery_rate_norm,
    #          cwnd_norm]
    state_dim = 5
    action_list = ["/2.0", "-10.0", "+0.0", "+10.0", "*2.0"]
    action_cnt = len(action_list)
    action_mapping = format_actions(action_list)

    def __init__(self, train):
    # public:
        self.cwnd = 10.0
        self.bytes_sent = 0
        self.ack_recv_ts = 0
        self.bytes_acked = 0

        # sender should stop or not
        self.stop_sender = False

    # private:
        self.train = train
        self.sample_action = None

        # step timer and counting
        self.step_start_ts = None
        self.step_num = 0

        # state related (persistent across steps)
        self.min_rtt = sys.maxint
        self.min_delay_ewma = float('inf')
        self.max_delay_ewma = 0.0
        self.min_send_rate_ewma = float('inf')
        self.max_send_rate_ewma = 0.0
        self.min_delivery_rate_ewma = float('inf')
        self.max_delivery_rate_ewma = 0.0

        # state related (reset at each step)
        self.rtt_ewma = None
        self.delay_ewma = None
        self.send_rate_ewma = None
        self.delivery_rate_ewma = None

# private
    def __update_state(self, ack):
        # update RTT and queuing delay (in ms)
        rtt = max(1, self.ack_recv_ts - ack.send_ts)
        self.min_rtt = min(self.min_rtt, rtt)
        self.rtt_ewma = update_ewma(self.rtt_ewma, rtt)

        queuing_delay = rtt - self.min_rtt
        self.delay_ewma = update_ewma(self.delay_ewma, queuing_delay)

        self.min_delay_ewma = min(self.min_delay_ewma, self.delay_ewma)
        self.max_delay_ewma = max(self.max_delay_ewma, self.delay_ewma)

        # update sending rate (in Mbps)
        send_rate = 0.008 * (self.bytes_sent - ack.bytes_sent) / rtt
        self.send_rate_ewma = update_ewma(self.send_rate_ewma, send_rate)

        self.min_send_rate_ewma = min(self.min_send_rate_ewma,
                                      self.send_rate_ewma)
        self.max_send_rate_ewma = max(self.max_send_rate_ewma,
                                      self.send_rate_ewma)

        # update delivery rate (in Mbps)
        duration = max(1, self.ack_recv_ts - ack.ack_recv_ts)
        delivery_rate = 0.008 * (self.bytes_acked - ack.bytes_acked) / duration
        self.delivery_rate_ewma = update_ewma(self.delivery_rate_ewma,
                                              delivery_rate)

        self.min_delivery_rate_ewma = min(self.min_delivery_rate_ewma,
                                          self.delivery_rate_ewma)
        self.max_delivery_rate_ewma = max(self.max_delivery_rate_ewma,
                                          self.delivery_rate_ewma)

    def __take_action(self, action):
        if action < 0 or action >= Policy.action_cnt:
            sys.exit('invalid action')

        op, val = Policy.action_mapping[action]
        self.cwnd = op(self.cwnd, val)
        self.cwnd = max(Policy.min_cwnd, min(Policy.max_cwnd, self.cwnd))

    # reset some stats at each step
    def __reset_step(self):
        self.rtt_ewma = None
        self.delay_ewma = None
        self.send_rate_ewma = None
        self.delivery_rate_ewma = None

    def __episode_ended(self):
        self.stop_sender = True

    def __step_ended(self):
        # normalization
        rtt_norm = self.rtt_ewma / Policy.max_rtt
        delay_norm = self.delay_ewma / Policy.max_delay
        send_rate_norm = self.send_rate_ewma / Policy.max_send_rate
        delivery_rate_norm = self.delivery_rate_ewma / Policy.max_delivery_rate
        cwnd_norm = self.cwnd / Policy.max_cwnd

        # state -> action
        state = [rtt_norm, delay_norm, send_rate_norm, delivery_rate_norm,
                 cwnd_norm]
        if self.sample_action is None:
            sys.exit('sample_action on policy has not been set')
        action = self.sample_action(state)

        self.__take_action(action)

        # reset at each step
        self.__reset_step()

        # step counting
        if self.train:
            self.step_num += 1
            if self.step_num >= Policy.steps_per_episode:
                self.__episode_ended()

# public:
    def ack_received(self, ack):
        self.ack_recv_ts = timestamp_ms()
        self.bytes_acked += Message.total_size

        self.__update_state(ack)

        # check if the current step is ended
        curr_ts = timestamp_ms()
        if self.step_start_ts is None:
            self.step_start_ts = curr_ts
        if curr_ts - self.step_start_ts > Policy.min_step_len:
            self.step_start_ts = curr_ts
            self.__step_ended()

    def data_sent(self, data):
        self.bytes_sent += Message.total_size

    def timeout_ms(self):
        return -1

    def set_sample_action(self, sample_action):
        self.sample_action = sample_action

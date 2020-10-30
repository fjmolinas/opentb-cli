#! /usr/bin/env python3

"""
Helper script to flash a hex to a set of OpenWSN OpenTestbed motes.

usage: opentestbed.py [-h] [--board BOARD] [--address ADDRESS] hexfile

positional arguments:
  hexfile               Hexfile program to bootload

optional arguments:
  -h, --help            show this help message and exit
  --board BOARD, --b BOARD
                        Board name (Only openmote-b is currently supported)
  --address ADDRESS, --a ADDRESS
                        Mote address in eui64 format(00-12-4b-00-14-b5-b4-98)or use "all" indicating all
                        motes)

example:

    python opentestbed.py -b openmote-b -a 00-12-4b-00-14-b5-b4-98 example/main.ihex
    python opentestbed.py -b openmote-b -a all example/main.ihex
"""

import argparse
import base64
import json
import logging
import os
import paho.mqtt.client as mqtt
import queue
import random
import re
import sys
import time

BROKER_ADDRESS = "argus.paris.inria.fr"

LOCAL_TEST_NODES = 4
NUMBER_OF_MOTES = 80 - LOCAL_TEST_NODES

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
LOG_LEVELS = ('debug', 'info', 'warning', 'error', 'fatal', 'critical')

USAGE_EXAMPLE = '''example:

    python opentestbed.py -b openmote-b -a 00-12-4b-00-14-b5-b4-98 example/main.ihex
    python opentestbed.py -b openmote-b -a all example/main.ihex
'''

PARSER = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_EXAMPLE)
PARSER.add_argument('hexfile', help='Hexfile program to bootload')
PARSER.add_argument( '--board', '--b', default='openmote-b',
                    help='Board name (Only openmote-b is currently supported)')
PARSER.add_argument('--address', '--a', nargs='+', default='all',
                    help='Mote address in eui64 format(00-12-4b-00-14-b5-b4-98)'
                         'or use "all" indicating all motes)')
PARSER.add_argument('--loglevel', choices=LOG_LEVELS, default='info',
                    help='Python logger log level')


class program_over_testbed(object):

    CLIENT_ID = 'OpenWSN'
    CMD = 'program'
    BASE_MOTE_TOPIC = 'opentestbed/deviceType/mote/deviceId'
    # in seconds, should be larger than the time starting from publishing
    # message until receiving the response
    RESPONSE_TIMEOUT = 40

    def __init__(self, motes, image_path):

        log = logging.getLogger("opentb")

        # initialize parameters
        self.motes      = motes
        self.image      = None

        # check bootload backdoor is configured correctly
        bootloader_backdoor_enabled   = False
        extended_linear_address_found = False

        with open(image_path,'r') as f:
            for line in f:

                # looking for data at address 0027FFD4
                # refer to: https://en.wikipedia.org/wiki/Intel_HEX#Record_types

                # looking for upper 16bit address 0027
                if line[:15] == ':020000040027D3':
                    extended_linear_address_found = True

                # check the lower 16bit address FFD4

                # | 1:3 byte count | 3:7 address | 9:17 32-bit field of the lock bit page (the last byte is backdoor configuration) |
                # 'F6' = 111        1                               0           110
                #        reserved   backdoor and bootloader enable  active low  PA pin used for backdoor enabling (PA6)
                if extended_linear_address_found and line[3:7] == 'FFD4' and int(line[1:3], 16)>4  and line[9:17] == 'FFFFFFF6':
                    bootloader_backdoor_enabled = True

        assert bootloader_backdoor_enabled

        self.image_name = ''
        with open(image_path,'rb') as f:
            self.image = base64.b64encode(f.read())
        if os.name=='nt':       # Windows
            self.image_name = image_path.split('\\')[-1]
        elif os.name=='posix':  # Linux
            self.image_name = image_path.split('/')[-1]

        # initialize statistic result
        self.response = {
            'success_count':   0 ,
            'msg_count':   0 ,
            'failed_msg_topic': [],
            'success_msg_topic': []
        }

        # connect to MQTT
        self.mqttclient                = mqtt.Client(self.CLIENT_ID)
        self.mqttclient.on_connect     = self._on_mqtt_connect
        self.mqttclient.on_message     = self._on_mqtt_message
        self.mqttclient.connect(BROKER_ADDRESS)
        self.mqttclient.loop_start()

        # create queue for receiving resp messages
        self.cmd_response_success_queue = queue.Queue()

        payload_program_image = {
            'token':       123,
            'description': self.image_name,
            'hex':         self.image.decode('utf-8'),
        }

        # publish
        for mote in self.motes:
            topic = '{}/{}/cmd/{}'.format(self.BASE_MOTE_TOPIC, mote ,
                                          self.CMD)
            self.mqttclient.publish(
                topic = topic,
                payload = json.dumps(payload_program_image)
                )

        # wait maximum RESPONSE_TIMEOUT seconds before return
        for _ in range(0, len(self.motes)):
            try:
                self.cmd_response_success_queue.get(timeout=self.RESPONSE_TIMEOUT)
            except queue.Empty as error:
                log.error("Response message timeout in {} seconds".format(
                    self.RESPONSE_TIMEOUT))

        # print results
        self.result()
        # cleanup
        self.mqttclient.loop_stop()


    def _mote_resp_topic(self, mote):
        return '{}/{}/resp/{}'.format(self.BASE_MOTE_TOPIC,
                                      mote, self.CMD)

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        log = logging.getLogger("opentb")
        log.info("connected to broker {}".format(BROKER_ADDRESS))
        if self.motes == ['all']:
            log.debug("bootloading all motes")
            topic = '{}/{}/resp/{}'.format(
                self.BASE_MOTE_TOPIC, '+', self.CMD)
            client.subscribe(topic)
        else:
            log.debug("bootloading motes: {}".format(self.motes))
            topics = [self._mote_resp_topic(mote) for mote in self.motes]
            for topic in topics:
                client.subscribe(topic)

        client.loop_start()

    def _on_mqtt_message(self, client, userdata, message):
        '''
        Record the number of message received and success status
        '''
        log = logging.getLogger("opentb")
        self.response['msg_count'] += 1

        log.debug("{}: responded {}".format(message.topic,
            json.loads(message.payload)))
        if json.loads(message.payload)['success']:
            self.response['success_count'] += 1
            self.response['success_msg_topic'].append(message.topic)
        else:
            self.response['failed_msg_topic'].append(message.topic)

        if self.motes == ['all']:
            if self.response['msg_count'] == NUMBER_OF_MOTES:
                self.cmd_response_success_queue.put('unblock')
        else:
            self.cmd_response_success_queue.put('unblock')

    def result(self):
        pattern = re.compile('{}/(.+)/resp/program'.format(self.BASE_MOTE_TOPIC))

        log = logging.getLogger("opentb")
        log.info("----------------------------------------------------")
        log.info("{} of {} motes reported with success".format(
            self.response['success_count'],
            self.response['msg_count']
        ))
        for topic in self.response['success_msg_topic']:
            mote = pattern.match(topic).group(1)
            log.info("    {} OK".format(mote))
        if self.response['msg_count'] > self.response['success_count']:
            for topic in self.response['failed_msg_topic']:
                mote = pattern.match(topic).group(1)
                log.info("    {} FAIL".format(mote))
        log.info("----------------------------------------------------")


def main(args=None):
    args = PARSER.parse_args()

    # setup logger
    log = logging.getLogger("opentb")
    if args.loglevel:
        loglevel = logging.getLevelName(args.loglevel.upper())
        log.setLevel(loglevel)

    log.addHandler(LOG_HANDLER)
    log.propagate = False

    configure = {}
    configure['board'] = args.board
    configure['mote_address'] = args.address
    configure['image_name_path'] = args.hexfile

    # program_over_testbed
    program_over_testbed(configure['mote_address'],
                         configure['image_name_path'])


if __name__ == "__main__":
    main()

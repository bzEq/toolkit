#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Helper script to run afl parallelly.

import os
import sys
import subprocess
import argparse
import shutil


def main():
    parser = argparse.ArgumentParser(description='Run afl parallelly')
    parser.add_argument('-e',
                        help='Path to afl',
                        default=shutil.which('afl-fuzz'))
    parser.add_argument('-j', type=int, default=1)
    parser.add_argument('-t', help='Target binary to fuzz', required=True)
    parser.add_argument('-i', required=True)
    parser.add_argument('-o', required=True)
    config = parser.parse_args()
    config.j = min(config.j, os.cpu_count())
    # Launch source process first.
    source_cmd = [
        config.e,
        '-b',
        '0',
        '-i',
        config.i,
        '-o',
        config.o,
        '-M',
        'fuzzer0',
        '--',
        config.t,
    ]
    source = subprocess.Popen(source_cmd)
    replicas = []
    for i in range(1, config.j):
        replica_cmd = [
            config.e,
            '-b',
            '%d' % i,
            '-i',
            config.i,
            '-o',
            config.o,
            '-S',
            'fuzzer%d' % i,
            '--',
            config.t,
        ]
        replicas.append(
            subprocess.Popen(replica_cmd,
                             stderr=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL))
    try:
        source.wait()
    except (Exception, KeyboardInterrupt):
        source.terminate()
    for p in replicas:
        p.terminate()


if __name__ == '__main__':
    sys.exit(main())

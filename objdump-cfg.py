#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# FIXME: A more reliable way to get this task done should be implementing the dumper in llvm-objdump.
import os
import sys
import re
import subprocess
import argparse
import shutil
import bisect
import logging
import io
import shlex

FUNCTION_BEGIN_LINE = re.compile(r'^([0-9a-f]+)\s+<(.+)>:$')
INSTRUCTION_LINE = re.compile(r'^\s*([0-9a-f]+):\s*(.*)$')
INSTRUCTION = re.compile(r'([^<#]+)(<([^\+]+)(\+([0-9a-fx]+))?>)?')

UNCONDITIONAL_BRANCHES = [
    re.compile(x) for x in [
        r'\bb\b',
        r'\bjmp\b',
        r'\bjmpq\b',
    ]
]
LONG_BRANCHES = [
    re.compile(x) for x in [
        r'\bblr\b',
        r'\bretq\b',
        r'\bret\b',
    ]
]


def IsUncondBr(s):
    for r in UNCONDITIONAL_BRANCHES:
        if r.search(s):
            return True
    return False


def IsLongBranch(s):
    for r in LONG_BRANCHES:
        if r.search(s):
            return True
    return False


def LowerBound(a, x, lo=0, hi=None, key=lambda x: x):
    l = lo
    r = hi if hi else len(a)
    mid = l + (r - l) // 2
    while l < r:
        if key(a[mid]) >= x:
            r = mid
        else:
            l = mid + 1
        mid = l + (r - l) // 2
    return r


def UpperBound(a, x, lo=0, hi=None, key=lambda x: x):
    l = lo
    r = hi if hi else len(a)
    mid = l + (r - l) // 2
    while l < r:
        if key(a[mid]) > x:
            r = mid
        else:
            l = mid + 1
        mid = l + (r - l) // 2
    return r


class Function(object):
    def __init__(self, name):
        self.name = name
        self.address = -1
        self.instructions = []

    def Append(self, address, instruction):
        self.instructions.append((address, instruction))

    def IsEmpty(self):
        return len(self.instructions) == 0


class CFGAnalyzer(object):
    def __init__(self, function, branch_analyzer):
        self.function = function
        self.branch_analyzer = branch_analyzer
        self.branch_targets = set()
        # List of (start_index_of_block, block_length).
        self.block_intervals = []
        self.preds = {}
        self.succs = {}

    def Analyze(self):
        if len(self.function.instructions) == 0:
            return
        # 1. Every branch target should start a new block.
        # 2. A block ends by either a branch or encounter a branch target.
        for b in self.branch_analyzer.branches:
            self.branch_targets.update(self.branch_analyzer.branches[b])
        # Add entry block to branch target for convenience.
        self.branch_targets.add(0)
        for t in self.branch_targets:
            self.preds[t] = set()
            self.succs[t] = set()
        i = -1
        for j in range(len(self.function.instructions)):
            if j in self.branch_targets:
                if i >= 0:
                    self.block_intervals.append((i, j - i))
                    # This should be fallthrough.
                    self.preds[j].add(i)
                    self.succs[i].add(j)
                i = j
            if j in self.branch_analyzer.branches:
                if i >= 0:
                    self.block_intervals.append((i, j - i + 1))
                    assert (i in self.branch_targets)
                    for branch in self.branch_analyzer.branches[j]:
                        assert (branch in self.branch_targets)
                        self.preds[branch].add(i)
                        self.succs[i].add(branch)
                i = -1


class GraphvizPainter(object):
    def __init__(self, function, cfg_analyzer):
        self.function = function
        self.cfg_analyzer = cfg_analyzer

    def Dot(self, out_stream, name='foo'):
        out_stream.write('digraph {} '.format(name))
        out_stream.write('{\n')
        out_stream.write('  node [shape="box", fontname="monospace"];\n')
        for bb in self.cfg_analyzer.block_intervals:
            bb_name = 'bb%d' % bb[0]
            instructions = '\\l'.join([
                '%x: %s' % (x[0], x[1])
                for x in self.function.instructions[bb[0]:bb[0] + bb[1]]
            ]) + '\\l'
            out_stream.write('  %s [label="%s"];\n' % (bb_name, instructions))
        for src in self.cfg_analyzer.succs:
            src_name = 'bb%d' % src
            for tgt in self.cfg_analyzer.succs[src]:
                tgt_name = 'bb%d' % tgt
                out_stream.write('  %s -> %s;\n' % (src_name, tgt_name))
        out_stream.write('}\n')


class BranchAnalyzer(object):
    def __init__(self, context, function):
        self.context = context
        self.function = function
        self.branches = {}

    def Analyze(self):
        logging.debug('Analyzing {}'.format(self.function.name))
        for i in range(len(self.function.instructions)):
            t = self.function.instructions[i]
            inst = t[1]
            m = INSTRUCTION.match(inst)
            assert (m)
            mg = m.groups()
            assert (len(mg) >= 1)
            inst_main = mg[0]
            if IsLongBranch(inst_main):
                self.branches[i] = []
            elif len(mg) >= 5 and mg[4]:
                # We might have encountered a branch. There is chance we get FP of branching.
                label = mg[2]
                offset = int(mg[4], 16)
                label_address = self.context.FindAddress(label)
                if label_address < 0:
                    continue
                targets = []
                index_of_address = self.findIndexOfAddress(label_address +
                                                           offset)
                if index_of_address >= 0:
                    targets.append(index_of_address)
                if not IsUncondBr(inst_main) and (i + 1) < len(
                        self.function.instructions):
                    # Fallthrough.
                    targets.append(i + 1)
                if not targets:
                    logging.debug(
                        '{} is branching to external function'.format(inst))
                self.branches[i] = targets

    def findIndexOfAddress(self, address):
        i = LowerBound(self.function.instructions, address, key=lambda t: t[0])
        if i == len(self.function.instructions
                    ) or self.function.instructions[i][0] != address:
            return -1
        return i


class ParseContext(object):
    def __init__(self, in_stream):
        self.current_function = ''
        self.functions = {}
        self.in_stream = in_stream

    def FindAddress(self, label):
        if label in self.functions:
            return self.functions[label].address
        return -1

    def Parse(self):
        for l in self.in_stream:
            self.parseLine(l)

    def parseLine(self, l):
        if not self.current_function:
            m = FUNCTION_BEGIN_LINE.match(l)
            if m:
                logging.debug('Found: {}'.format(m.group(2)))
                self.current_function = Function(m.group(2))
                self.current_function.address = int(m.group(1), 16)
                self.functions[
                    self.current_function.name] = self.current_function
        else:
            m = INSTRUCTION_LINE.match(l)
            if not m:
                # Current function ends.
                self.current_function = None
            else:
                address = int(m.group(1), 16)
                instruction = m.group(2)
                self.current_function.Append(address, instruction)


def main():
    parser = argparse.ArgumentParser(
        description='Output CFG dot file via objdump.')
    parser.add_argument('--objdump', default=shutil.which('objdump'))
    parser.add_argument('--debug', default=False, action='store_true')
    parser.add_argument('--func', required=True)
    parser.add_argument('-o', dest='out')
    parser.add_argument('obj', nargs=1)
    config = parser.parse_args()
    if config.debug:
        logging.basicConfig(level=logging.DEBUG)
    cmd = [
        config.objdump,
        '-d',
        '--no-show-raw-insn',
        config.obj[0],
    ]
    cp = subprocess.run(cmd, capture_output=True)
    if cp.returncode != 0:
        logging.error('Failed to run {}'.format(cmd))
        return cp.returncode
    context = ParseContext(io.StringIO(cp.stdout.decode('utf-8')))
    context.Parse()
    if config.func not in context.functions:
        logging.error("Can't find {} in the object file".format(config.func))
        return 1
    function = context.functions[config.func]
    BA = BranchAnalyzer(context, function)
    BA.Analyze()
    logging.debug("{}'s branches: {}".format(function.name, BA.branches))
    CA = CFGAnalyzer(function, BA)
    CA.Analyze()
    GVP = GraphvizPainter(function, CA)
    logging.debug("{}'s basic block layout: {}".format(function.name,
                                                       CA.block_intervals))
    if not config.out:
        GVP.Dot(sys.stdout)
    else:
        with open(config.out, 'w') as out:
            GVP.Dot(out)


if __name__ == '__main__':
    sys.exit(main())

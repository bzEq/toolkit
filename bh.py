#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import subprocess
import shutil
import shlex
import logging


class Builder(object):
    def __init__(self,
                 src_dir,
                 build_dir,
                 install_prefix,
                 cmake=shutil.which('cmake'),
                 ninja=shutil.which('ninja'),
                 build_type='Release',
                 cc=shutil.which('gcc'),
                 ld=shutil.which('ld'),
                 cflags=[],
                 cxxflags=[],
                 projects=[],
                 runtimes=[],
                 extra_cmake_flags=[],
                 config_only=False,
                 ninja_targets=[]):
        self.cmake = cmake
        self.ninja = ninja
        self.src_dir = os.path.abspath(src_dir)
        self.build_dir = os.path.abspath(build_dir)
        self.install_prefix = os.path.abspath(install_prefix)
        self.cc = os.path.abspath(cc)
        self.ld = os.path.abspath(ld)
        self.cflags = cflags
        self.cxxflags = cxxflags
        self.projects = projects
        self.runtimes = runtimes
        self.extra_cmake_flags = extra_cmake_flags
        self.ninja_targets = ninja_targets
        self.build_type = build_type
        self.config_only = config_only

    def ConfigAndBuild(self):
        err = self.Config()
        if self.config_only:
            return err
        return self.Build()

    def Config(self):
        cmake_command = [
            self.cmake,
            '-GNinja',
            '-DCMAKE_MAKE_PROGRAM={}'.format(self.ninja),
            '-DLLVM_ENABLE_ASSERTIONS=On',
            '-DCMAKE_BUILD_TYPE={}'.format(self.build_type),
            '-DCMAKE_INSTALL_PREFIX={}'.format(self.install_prefix),
            '-DCMAKE_C_COMPILER={}'.format(self.cc),
            '-DLLVM_USE_LINKER={}'.format(self.ld),
        ]
        if self.cflags:
            cmake_command.append('-DCMAKE_C_FLAGS={}'.format(
                shlex.join(self.cflags)))
        if self.cxxflags:
            cmake_command.append('-DCMAKE_CXX_FLAGS={}'.format(
                shlex.join(self.cxxflags)))
        if self.projects:
            cmake_command.append('-DLLVM_ENABLE_PROJECTS={}'.format(';'.join(
                self.projects)))
        if self.runtimes:
            cmake_command.append('-DLLVM_ENABLE_RUNTIMES={}'.format(';'.join(
                self.runtimes)))
        cmake_command.extend(self.extra_cmake_flags)
        cmake_command.append(self.src_dir)
        err = subprocess.call(cmake_command, cwd=self.build_dir)
        if err != 0:
            logging.error('cmake failed: {}'.format(cmake_command))
        return err

    def Build(self):
        ninja_command = [
            self.ninja,
        ]
        ninja_command.extend(self.ninja_targets)
        err = subprocess.call(ninja_command, cwd=self.build_dir)
        if err != 0:
            logging.error('ninja failed: {}'.format(ninja_command))
        return err


def AddCommonArguments(parser):
    parser.add_argument('--cmake_binary', default=shutil.which('cmake'))
    parser.add_argument('--ninja_binary', default=shutil.which('ninja'))
    parser.add_argument('--install_prefix', required=True)
    parser.add_argument('--src_dir', required=True)
    parser.add_argument('--build_dir', required=True)


def BuildProduct(config):
    extra_cmake_flags = []
    if config.dylib:
        extra_cmake_flags.append('-DLLVM_LINK_LLVM_DYLIB=ON')
    if config.binutils_include:
        extra_cmake_flags.append('-DLLVM_BINUTILS_INCDIR={path}'.format(
            path=config.binutils_include))
    if config.use_newpm:
        extra_cmake_flags.append('-DLLVM_USE_NEWPM=On')
    if config.llvm_toolchain:
        # All in llvm's toolchain.
        extra_cmake_flags.append('-DCLANG_DEFAULT_LINKER=lld')
        extra_cmake_flags.append('-DCLANG_DEFAULT_CXX_STDLIB=libc++')
        extra_cmake_flags.append('-DCLANG_DEFAULT_RTLIB=compiler-rt')
        extra_cmake_flags.append('-DLIBCXX_USE_COMPILER_RT=YES')
        extra_cmake_flags.append('-DLIBCXXABI_USE_COMPILER_RT=YES')
        extra_cmake_flags.append('-DLIBCXXABI_USE_LLVM_UNWINDER=YES')
        extra_cmake_flags.append('-DLIBOMP_LIBFLAGS=-lm')
        # FIXME: Current build fails if turning this on.
        extra_cmake_flags.append('-DOPENMP_ENABLE_LIBOMPTARGET=OFF')
    if config.enable_atomic:
        extra_cmake_flags.append('-DCOMPILER_RT_EXCLUDE_ATOMIC_BUILTIN=NO')
    builder = Builder(config.src_dir,
                      config.build_dir,
                      config.install_prefix,
                      cc=config.clang,
                      ld=config.lld,
                      config_only=options.config_only,
                      runtimes=[
                          'compiler-rt',
                          'libcxx',
                          'libcxxabi',
                          'libunwind',
                          'openmp',
                      ],
                      projects=[
                          'clang',
                          'clang-tools-extra',
                          'lld',
                          'lldb',
                          'mlir',
                          'polly',
                      ],
                      extra_cmake_flags=extra_cmake_flags)
    return builder.ConfigAndBuild()


def BuildForDev(options):
    build_type = 'Release'
    if options.debug:
        build_type = 'Debug'
    extra_cmake_flags = []
    if options.build_shared_libs:
        extra_cmake_flags.append('-DBUILD_SHARED_LIBS=On')
    builder = Builder(options.src_dir,
                      options.build_dir,
                      options.install_prefix,
                      build_type=build_type,
                      cc=options.clang,
                      extra_cmake_flags=extra_cmake_flags,
                      config_only=options.config_only,
                      runtimes=['compiler-rt'],
                      projects=['clang', 'lld'])
    return builder.ConfigAndBuild()


def BootstrapMinimal(config):
    stage1_path = os.path.join(os.path.abspath(config.build_dir), 'stage1')
    stage2_path = os.path.join(os.path.abspath(config.build_dir), 'stage2')
    os.makedirs(stage1_path, exist_ok=True)
    os.makedirs(stage2_path, exist_ok=True)
    # Stage1.
    stage1_builder = Builder(config.src_dir,
                             stage1_path,
                             config.install_prefix,
                             cc=config.bootstrap_cc,
                             projects=['clang', 'lld'])
    err = stage1_builder.ConfigAndBuild()
    if err != 0:
        logging.error("Stage1 failed")
        return err
    if config.skip_stage2:
        return err
    stage2_builder = Builder(config.src_dir,
                             stage2_path,
                             config.install_prefix,
                             cc=os.path.join(stage1_path, 'bin', 'clang'),
                             ld=os.path.join(stage1_path, 'bin', 'ld.lld'),
                             projects=['clang', 'clang-tools-extra', 'extra'],
                             runtimes=['compiler-rt'])
    return stage2_builder.ConfigAndBuild()


def BuildWithPGOAndLTO(config):
    pass1_path = os.path.join(config.build_dir, 'pass1')
    pass2_path = os.path.join(config.build_dir, 'pass2')
    test_suite_path = os.path.join(config.build_dir, 'test-suite-build')
    os.makedirs(pass1_path, exist_ok=True)
    os.makedirs(pass2_path, exist_ok=True)
    os.makedirs(test_suite_path, exist_ok=True)
    # Pass1.
    if config.skip_pass1:
        pass1_extra_cmake_flags = []
        pass1_builder = Builder(config.src_dir,
                                pass1_path,
                                config.install_prefix,
                                projects=['clang'])
        err = pass1_builder.ConfigAndBuild()
        if err != 0:
            logging.error("Pass1 failed.")
            return err
    # Pass2.
    pass2_extra_cmake_flags = []
    pass2_builder = Builder(config.src_dir,
                            pass2_path,
                            config.install_prefix,
                            projects=[],
                            runtimes=[])
    return pass2_builder.ConfigAndBuild()


def main():
    parser = argparse.ArgumentParser(
        description='Build Clang/LLVM under different mode.')
    subparsers = parser.add_subparsers(required=True, dest='cmd')

    # Bootstrap build.
    bootstrap_minimal = subparsers.add_parser(
        'bootstrap_minimal',
        description='Bootstrap a minimal Clang/LLVM toolchain.')
    AddCommonArguments(bootstrap_minimal)
    bootstrap_minimal.add_argument('--bootstrap_cc',
                                   default=shutil.which('gcc'))
    bootstrap_minimal.add_argument('--skip_stage2',
                                   default=False,
                                   action='store_true')
    bootstrap_minimal.add_argument('--skip_stage2_test',
                                   action='store_true',
                                   default=False)
    bootstrap_minimal.set_defaults(func=BootstrapMinimal)

    # Dev build.
    dev = subparsers.add_parser('dev',
                                description='Build Clang/LLVM for developing.')
    AddCommonArguments(dev)
    dev.add_argument('--clang', default=shutil.which('clang'))
    dev.add_argument('--lld', default=shutil.which('ld.lld'))
    dev.add_argument('--debug', action='store_true', default=False)
    dev.add_argument('--config_only', action='store_true', default=False)
    dev.add_argument('--skip_test', action='store_true', default=False)
    dev.add_argument('--skip_package', action='store_true', default=False)
    dev.add_argument('--build_shared_libs', action='store_true', default=False)
    dev.set_defaults(func=BuildForDev)

    # Prod build.
    prod = subparsers.add_parser(
        'prod', description='Build Clang/LLVM toolchain as product.')
    # See https://clang.llvm.org/docs/Toolchain.html.
    prod.add_argument('--clang', default=shutil.which('clang'))
    prod.add_argument('--lld', default=shutil.which('ld.lld'))
    prod.add_argument('--llvm_toolchain', action='store_true', default=False)
    prod.add_argument('--use_newpm', action='store_true', default=False)
    prod.add_argument('--enable_atomic', action='store_true', default=False)
    prod.add_argument('--dylib', action='store_true', default=False)
    prod.add_argument('--binutils_include')
    AddCommonArguments(prod)
    prod.set_defaults(func=BuildProduct)

    # PGO/LTO build.
    opt = subparsers.add_parser(
        'opt', description='Build PGO/LTOed Clang/LLVM toolchain.')
    AddCommonArguments(opt)
    opt.add_argument('--llvm_path', required=True)
    opt.add_argument('--native', action='store_true', default=False)
    opt.add_argument('--skip_pass1', action='store_true', default=False)
    opt.add_argument('--llvm_test_suite_path')
    opt.set_defaults(func=BuildWithPGOAndLTO)

    args = parser.parse_args()
    if args.func:
        return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

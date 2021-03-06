#!/usr/bin/python2
# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2016  Marek Marczykowski-Górecki
#                                   <marmarek@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.
#
import logging
import multiprocessing
import os
import pipes
import tempfile
import shutil
import subprocess
import sys
import time
import yaml
import salt.client
import salt.config
import qubes.exc
import qubes.mgmt.patches
import qubes.vm.dispvm

FORMAT_LOG = '%(asctime)s %(message)s'
LOGPATH = '/var/log/qubes'

formatter_log = logging.Formatter(FORMAT_LOG)

class ManageVM(object):
    def __init__(self, app, vm, mgmt_template=None):
        super(ManageVM, self).__init__()
        self.vm = vm
        self.app = app
        self.log = logging.getLogger('qubessalt.vm.' + vm.name)
        handler_log = logging.FileHandler(
            os.path.join(LOGPATH, 'mgmt-{}.log'.format(vm.name)),
            encoding='utf-8')
        handler_log.setFormatter(formatter_log)
        self.log.addHandler(handler_log)

        self.log.propagate = False
        if mgmt_template is not None:
            self.mgmt_template = mgmt_template
        else:
            self.mgmt_template = self.app.default_dispvm

    def prepare_salt_config_for_vm(self):
        tmpdir = tempfile.mkdtemp()
        output_dir = os.path.join(tmpdir, 'srv')
        shutil.copytree('/srv', output_dir)
        # make sure only pillars for given host are send

        p = subprocess.Popen(
            ['qubesctl', '--dom0-only',
                '--id={}'.format(self.vm.name), '--output=yaml',
                'pillar.items'], stdout=subprocess.PIPE)
        (pillar_items_output, _) = p.communicate()
        pillar_data = yaml.safe_load(pillar_items_output)
        pillar_data = pillar_data['local']
        # remove source pillar files
        # TODO: remove also pillar modules
        for _, roots in pillar_data['master']['pillar_roots'].iteritems():
            for root in roots:
                # do not use os.path.join on purpose - root is absolute path
                pillar_path = tmpdir + root
                if os.path.exists(pillar_path):
                    shutil.rmtree(tmpdir + root)

        # pass selected configuration options
        master_conf = {}
        for opt in ['file_roots']:
            if opt in pillar_data['master'].keys():
                master_conf[opt] = pillar_data['master'][opt]
        with open(os.path.join(output_dir, 'master'), 'w') as f:
            f.write(yaml.dump(master_conf))

        # remove unneded pillar entries
        for entry in ['master', 'salt']:
            if entry in pillar_data.keys():
                del pillar_data[entry]

        # save rendered pillar data
        pillar_dir = os.path.join(output_dir, 'pillar')
        os.mkdir(pillar_dir)
        with open(os.path.join(pillar_dir, 'combined.sls'), 'w') as f:
            f.write(yaml.dump(pillar_data))
        # TODO only selected environments?
        with open(os.path.join(pillar_dir, 'top.sls'), 'w') as f:
            f.write(yaml.dump(
                {'base':
                    {self.vm.name: ['combined']}}))
        return output_dir

    def salt_call(self, command='state.highstate', return_output=False):
        self.log.info('calling \'%s\'...', command)
        appvmtpl = self.mgmt_template

        name = 'disp-mgmt-{}'.format(self.vm.name)
        # name is limited to 31 chars
        if len(name) > 31:
            name = name[:31]
        try:
            dispvm = self.app.domains[name]
        except KeyError:
            dispvm = qubes.vm.dispvm.DispVM.from_appvm(
                appvmtpl,
                name=name,
                netvm=None,
                internal=True)
            create = True
            self.app = dispvm.app
        else:
            create = False
        qrexec_policy(dispvm.name, self.vm.name, True)
        return_data = "NO RESULT"
        try:
            initially_running = self.vm.is_running()
            if not dispvm.is_running():
                dispvm.start(start_guid=False)
            # Copy whole Salt configuration
            salt_config = self.prepare_salt_config_for_vm()
            retcode = dispvm.run_service(
                'qubes.Filecopy',
                localcmd='/usr/lib/qubes/qfile-dom0-agent {}'.format(
                    salt_config),
                gui=False)
            shutil.rmtree(salt_config)
            if retcode != 0:
                raise qubes.exc.QubesException(
                    "Failed to copy Salt configuration to {}".
                    format(dispvm.name))
            p = dispvm.run_service('qubes.SaltLinuxVM', passio_popen=True,
                gui=False)
            (stdout, _) = p.communicate(self.vm.name + '\n' + command + '\n')
            for line in stdout.splitlines():
                self.log.info('output: %s', line)
            self.log.info('exit code: %d', p.returncode)
            if return_output and stdout:
                lines = stdout.splitlines()
                if lines[0].count(self.vm.name + ':') == 1:
                    lines = lines[1:]
                return_data = lines
            else:
                return_data = "OK" if p.returncode == 0 else "ERROR"
            if self.vm.is_running() and not initially_running:
                self.vm.shutdown()
                # FIXME: convert to self.vm.shutdown(wait=True) in core3
                while self.vm.is_running():
                    time.sleep(1)
        finally:
            qrexec_policy(dispvm.name, self.vm.name, False)
            dispvm.cleanup()
        return return_data


def run_one(vmname, command, show_output):
    app = qubes.Qubes()
    try:
        vm = app.domains[vmname]
    except KeyError:
        return vmname, "ERROR (vm not found)"
    runner = ManageVM(app, vm)
    try:
        result = runner.salt_call(
            ' '.join([pipes.quote(word) for word in command]),
            return_output=show_output)
    except Exception as e:  # pylint: disable=broad-except
        return vmname, "ERROR (exception {})".format(str(e))
    return vm.name, result


class ManageVMRunner(object):
    """Call salt in multiple VMs at the same time"""

    def __init__(self, app, vms, command, max_concurrency=4, show_output=False,
            force_color=False):
        super(ManageVMRunner, self).__init__()
        self.vms = vms
        self.app = app
        self.command = command
        self.max_concurrency = max_concurrency
        self.show_output = show_output
        self.force_color = force_color
        self._opts = salt.config.minion_config('/etc/salt/minion')
        self._opts['file_client'] = 'local'

        # this do patch already imported salt modules
        import qubes.mgmt.patches

    def collect_result(self, result_tuple):
        name, result = result_tuple
        if self.show_output and isinstance(result, list):
            sys.stdout.write(name + ":\n")
            # removing control characters, unless colors are enabled
            if not self.force_color:
                result = [
                    ''.join([c for c in line if ord(c) >= 0x20]) for line in
                    result]
            sys.stdout.write('\n'.join(['  ' + line for line in result]))
            sys.stdout.write('\n')
        else:
            print name + ": " + result

    def has_config(self, vm):
        opts = self._opts.copy()
        opts['id'] = vm.name
        caller = salt.client.Caller(mopts=opts)
        top = caller.function('state.show_top')
        return bool(top)

    def run(self):
        pool = multiprocessing.Pool(self.max_concurrency)
        for vm in self.vms:
            # TODO: add some override for this check
            if self.has_config(vm):
                pool.apply_async(run_one,
                    (vm.name, self.command, self.show_output),
                    callback=self.collect_result
                )
            else:
                self.collect_result((vm.name, "SKIP (nothing to do)"))
        pool.close()
        pool.join()


def qrexec_policy(src, dst, allow):
    for service in ('qubes.Filecopy', 'qubes.VMShell'):
        with open('/etc/qubes-rpc/policy/{}'.format(service), 'r+') as policy:
            policy_rules = policy.readlines()
            line = "{} {} allow,user=root\n".format(src, dst)
            if allow:
                policy_rules.insert(0, line)
            else:
                policy_rules.remove(line)
            policy.truncate(0)
            policy.seek(0)
            policy.write(''.join(policy_rules))

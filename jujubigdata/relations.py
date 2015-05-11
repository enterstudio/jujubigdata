# Copyright 2014-2015 Canonical Limited.
#
# This file is part of jujubigdata.
#
# jujubigdata is free software: you can redistribute it and/or modify
# it under the terms of the Apache License version 2.0.
#
# jujubigdata is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# Apache License for more details.

import json
from subprocess import check_call

from path import Path

from charmhelpers.core import host
from charmhelpers.core import hookenv
from charmhelpers.core.charmframework.helpers import Relation, any_ready_unit

from jujubigdata import utils


class SpecMatchingRelation(Relation):
    """
    Relation base class that validates that a version and environment
    between two related charms match, to prevent interoperability issues.

    This class adds a ``spec`` key to the ``required_keys`` and populates it
    in :meth:`provide`.  The ``spec`` value must be passed in to :meth:`__init__`.

    The ``spec`` should be a mapping (or a callback that returns a mapping)
    which describes all aspects of the charm's environment or configuration
    that might affect its interoperability with the remote charm.  The charm
    on the requires side of the relation will verify that all of the keys in
    its ``spec`` are present and exactly equal on the provides side of the
    relation.  This does mean that the requires side can be a subset of the
    provides side, but not the other way around.

    An example spec string might be::

        {
            'arch': 'x86_64',
            'vendor': 'apache',
            'version': '2.4',
        }
    """
    def __init__(self, spec=None, *args, **kwargs):
        """
        Create a new relation handler instance.

        :param str spec: Spec string that should capture version or environment
            particulars which can cause issues if mismatched.
        """
        super(SpecMatchingRelation, self).__init__(*args, **kwargs)
        self._spec = spec

    @property
    def spec(self):
        if callable(self._spec):
            return self._spec()
        return self._spec

    def provide(self, remote_service, all_ready):
        """
        Provide the ``spec`` data to the remote service.

        Subclasses *must* either delegate to this method (e.g., via `super()`)
        or include ``'spec': json.dumps(self.spec)`` in the provided data themselves.
        """
        data = super(SpecMatchingRelation, self).provide(remote_service, all_ready)
        if self.spec:
            data['spec'] = json.dumps(self.spec)
        return data

    def filtered_data(self, remote_service=None):
        if self.spec and 'spec' not in self.required_keys:
            self.required_keys.append('spec')
        return super(SpecMatchingRelation, self).filtered_data(remote_service)

    def is_ready(self):
        """
        Validate the ``spec`` data from the connected units to ensure that
        it matches the local ``spec``.
        """
        if not super(SpecMatchingRelation, self).is_ready():
            return False
        if not self.spec:
            return True
        for unit, data in self.filtered_data().items():
            remote_spec = json.loads(data.get('spec', '{}'))
            for k, v in self.spec.items():
                if v != remote_spec.get(k):
                    # TODO XXX Once extended status reporting is available,
                    #          we should use that instead of erroring.
                    raise ValueError(
                        'Spec mismatch with related unit %s: '
                        '%r != %r' % (unit, data.get('spec'), json.dumps(self.spec)))
        return True


class NameNode(SpecMatchingRelation):
    """
    Relation which communicates the NameNode (HDFS) connection & status info.

    This is the relation that clients should use.
    """
    relation_name = 'namenode'
    required_keys = ['private-address', 'port', 'webhdfs-port', 'ready']

    def __init__(self, spec=None, hdfs_port=None, webhdfs_port=None):
        self.hdfs_port = hdfs_port  # only needed for provides
        self.webhdfs_port = webhdfs_port  # only needed for provides
        super(NameNode, self).__init__(spec)

    def provide(self, remote_service, all_ready):
        data = super(NameNode, self).provide(remote_service, all_ready)
        if all_ready and DataNode().is_ready():
            utils.wait_for_hdfs(400)  # will error on timeout
            data.update({
                'ready': 'true',
                'port': self.hdfs_port,
                'webhdfs_port': self.webhdfs_port,
            })
        return data


class ResourceManager(SpecMatchingRelation):
    """
    Relation which communicates the ResourceManager (YARN) connection & status info.

    This is the relation that clients should use.
    """
    relation_name = 'resourcemanager'
    required_keys = ['private-address', 'port', 'historyserver-port', 'ready']

    def __init__(self, spec=None, port=None, historyserver_port=None):
        self.port = port  # only needed for provides
        self.historyserver_port = historyserver_port  # only needed for provides
        super(ResourceManager, self).__init__(spec)

    def provide(self, remote_service, all_ready):
        data = super(ResourceManager, self).provide(remote_service, all_ready)
        if all_ready:
            data.update({
                'ready': 'true',
                'port': self.port,
                'historyserver-port': self.historyserver_port,
            })
        return data


class DataNode(Relation):
    """
    Relation which communicates DataNode info back to NameNodes.
    """
    relation_name = 'datanode'
    required_keys = ['private-address', 'hostname', 'hostfqdn']

    def provide(self, remote_service, all_ready):
        data = super(DataNode, self).provide(remote_service, all_ready)
        host, fqdn = utils.get_hostname_data()
        data.update({
            'hostname': host,
            'hostfqdn': fqdn,
        })
        return data


class NameNodeMaster(NameNode):
    """
    Alternate NameNode relation for DataNodes.
    """
    relation_name = 'datanode'

    def provide(self, remote_service, all_ready):
        data = super(NameNodeMaster, self).provide(remote_service, all_ready)
        if all_ready:
            data['ready'] = 'true'
        return data


class NodeManager(Relation):
    """
    Relation which communicates NodeManager info back to ResourceManagers.
    """
    relation_name = 'nodemanager'
    required_keys = ['private-address', 'hostname', 'hostfqdn']

    def provide(self, remote_service, all_ready):
        data = super(NodeManager, self).provide(remote_service, all_ready)
        host, fqdn = utils.get_hostname_data()
        data.update({
            'hostname': host,
            'hostfqdn': fqdn,
        })
        return data


class ResourceManagerMaster(ResourceManager):
    """
    Alternate ResourceManager relation for NodeManagers.
    """
    relation_name = 'nodemanager'
    required_keys = ['private-address', 'ssh-key', 'ready']

    def get_ssh_key(self):
        sshdir = Path('/home/ubuntu/.ssh')
        keyfile = sshdir / 'id_rsa'
        pubfile = sshdir / 'id_rsa.pub'
        if not pubfile.exists():
            (sshdir / 'config').write_lines([
                'Host *',
                '    StrictHostKeyChecking no'
            ], append=True)
            check_call(['ssh-keygen', '-t', 'rsa', '-P', '', '-f', keyfile])
            host.chownr(sshdir, 'ubuntu', 'hadoop')
        return pubfile.text()

    def provide(self, remote_service, all_ready):
        data = super(ResourceManagerMaster, self).provide(remote_service, all_ready)
        data.update({
            'ssh-key': self.get_ssh_key(),
        })
        return data

    def install_ssh_keys(self):
        ssh_keys = self.filtered_relation_data().values()
        Path('/home/ubuntu/.ssh/authorized_keys').write_lines(ssh_keys, append=True)


class HadoopPlugin(Relation):
    """
    This helper class manages the ``hadoop-plugin`` interface, and
    is the recommended way of interacting with the endpoint via this
    interface.

    Charms using this interface will have a JRE installed, the Hadoop
    API Java libraries installed, the Hadoop configuration managed in
    ``/etc/hadoop/conf``, and the environment configured in ``/etc/environment``.
    The endpoint will ensure that the distribution, version, Java, etc. are all
    compatible to ensure a properly functioning Hadoop ecosystem.

    Charms using this interface should call :meth:`hdfs_is_ready` to
    determine if this relation is ready to use.
    """
    relation_name = 'hadoop-plugin'
    required_keys = ['hdfs-ready']
    '''
    This key will be set on the relation once everything is installed,
    configured, connected, and ready to receive work.  This key can be
    checked by calling :meth:`hdfs_is_ready`, or manually via Juju's
    ``relation-get``.
    '''

    def __init__(self, *args, **kwargs):
        super(HadoopPlugin, self).__init__(*args, **kwargs)

    def provide(self, remote_service, all_ready):
        """
        Used by the endpoint to provide the :attr:`required_keys`.
        """
        if not all_ready:
            return {}
        utils.wait_for_hdfs(400)  # will error if timeout
        return {'hdfs-ready': True}

    def hdfs_is_ready(self):
        """
        Check if the Hadoop libraries and installed and configured and HDFS is
        connected and ready to handle work (at least one DataNode available).

        (This is a synonym for ``self.is_ready()``.)
        """
        return self.is_ready()


class HadoopREST(Relation):
    """
    This helper class manages the ``hadoop-rest`` interface, and
    is the recommended way of interacting with the endpoint via this
    interface.

    Charms using this interface are provided with the API endpoint
    information for the NameNode, ResourceManager, and JobHistoryServer.
    """
    relation_name = 'hadoop-rest'
    required_keys = ['namenode-host', 'hdfs-port', 'webhdfs-port',
                     'resourcemanager-host', 'resourcemanager-port',
                     'historyserver-host', 'historyserver-port']

    def provide(self, remote_service, all_ready):
        """
        Used by the endpoint to provide the :attr:`required_keys`.
        """
        if not all_ready:
            return {}
        namenode = any_ready_unit(NameNode.relation_name)
        resourcemanager = any_ready_unit(ResourceManager.relation_name)
        return {
            'namenode-host': namenode['private-address'],
            'hdfs-port': namenode['port'],
            'webhdfs-port': namenode['webhdfs-port'],
            'resourcemanager-host': resourcemanager['private-address'],
            'resourcemanager-port': resourcemanager['port'],
            'historyserver-host': resourcemanager['private-address'],
            'historyserver-port': resourcemanager['historyserver-port'],
        }

    def _get(self, *keys):
        if not self.is_ready():
            return None
        data = self.filtered_data().values()[0]
        if not keys:
            return None
        elif len(keys) == 1:
            return data[keys[0]]
        else:
            return [data[key] for key in keys]

    @property
    def namenode_host(self):
        'Property containing the NameNode host, or ``None`` if not available.'
        return self._get('namenode-host')

    @property
    def hdfs_port(self):
        'Property containing the HDFS port, or ``None`` if not available.'
        return self._get('hdfs-port')

    @property
    def webhdfs_port(self):
        'Property containing the WebHDFS port, or ``None`` if not available.'
        return self._get('webhdfs-port')

    @property
    def resourcemanager_host(self):
        'Property containing the ResourceManager host, or ``None`` if not available.'
        return self._get('resourcemanager-host')

    @property
    def resourcemanager_port(self):
        'Property containing the ResourceManager port, or ``None`` if not available.'
        return self._get('resourcemanager-port')

    @property
    def historyserver_host(self):
        'Property containing the HistoryServer host, or ``None`` if not available.'
        return self._get('historyserver-host')

    @property
    def historyserver_port(self):
        'Property containing the HistoryServer port, or ``None`` if not available.'
        return self._get('historyserver-port')

    @property
    def hdfs_uri(self):
        'Property containing the full HDFS URI, or ``None`` if not available.'
        host, port = self._get('namenode-host', 'hdfs-port')
        if host and port:
            return 'hdfs://{}:{}'.format(host, port)
        else:
            return None

    @property
    def webhdfs_uri(self):
        'Property containing the full WebHDFS URI, or ``None`` if not available.'
        host, port = self._get('namenode-host', 'webhdfs-port')
        if host and port:
            return 'http://{}:{}/webhdfs/v1'.format(host, port)
        else:
            return None

    @property
    def resourcemanager_uri(self):
        'Property containing the full ResourceManager API URI, or ``None`` if not available.'
        host, port = self._get('resourcemanager-host', 'resourcemanager-port')
        if host and port:
            return 'http://{}:{}'.format(host, port)
        else:
            return None

    @property
    def historyserver_uri(self):
        'Property containing the full JobHistoryServer API URI, or ``None`` if not available.'
        host, port = self._get('historyserver-host', 'historyserver-port')
        if host and port:
            return 'http://{}:{}'.format(host, port)
        else:
            return None


class MySQL(Relation):
    relation_name = 'db'
    required_keys = ['host', 'database', 'user', 'password']


class FlumeAgent(Relation):
    relation_name = 'flume-agent'
    required_keys = ['private-address', 'port']

    def provide(self, remote_service, all_ready):
        data = super(FlumeAgent, self).provide(remote_service, all_ready)
        flume_protocol = hookenv.config('protocol')
        if (flume_protocol not in ['avro']):
            hookenv.log('Invalid flume protocol {}'.format(flume_protocol), hookenv.ERROR)
            return data
        data.update({
            'protocol': hookenv.config('protocol'),
        })
        return data


class Hive(Relation):
    relation_name = 'hive'
    required_keys = ['private-address', 'port', 'ready']

    def __init__(self, port=None):
        self.port = port  # only needed for provides
        super(Hive, self).__init__()

    def provide(self, remote_service, all_ready):
        data = super(Hive, self).provide(remote_service, all_ready)
        if all_ready:
            data.update({
                'ready': 'true',
                'port': self.port,
            })
        return data

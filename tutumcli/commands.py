from __future__ import print_function
import getpass
import ConfigParser
import json
import sys
import re
import os
from os.path import join, expanduser, abspath, isfile

import yaml
import tutum
import docker
from tutum.api import auth
from tutum.api import exceptions
from exceptions import StreamOutputError, ObjectNotFound
from tutumcli import utils


TUTUM_FILE = '.tutum'
AUTH_SECTION = 'auth'
USER_OPTION = "user"
APIKEY_OPTION = 'apikey'
AUTH_ERROR = 'auth_error'
NO_ERROR = 'no_error'

TUTUM_AUTH_ERROR_EXIT_CODE = 2
EXCEPTION_EXIT_CODE = 3


def login():
    username = raw_input("Username: ")
    password = getpass.getpass()
    try:
        user, api_key = auth.get_auth(username, password)
        if api_key is not None:
            config = ConfigParser.ConfigParser()
            config.add_section(AUTH_SECTION)
            config.set(AUTH_SECTION, USER_OPTION, user)
            config.set(AUTH_SECTION, APIKEY_OPTION, api_key)
            with open(join(expanduser('~'), TUTUM_FILE), 'w') as cfgfile:
                config.write(cfgfile)
            print("Login succeeded!")
    except exceptions.TutumAuthError:
        registered, text = utils.try_register(username, password)
        if registered:
            print(text)
        else:
            if 'username: A user with that username already exists.' in text:
                print("Wrong username and/or password. Please try to login again", file=sys.stderr)
                sys.exit(TUTUM_AUTH_ERROR_EXIT_CODE)
            else:
                text = text.replace('password1', 'password')
                text = text.replace('password2', 'password')
                text = text.replace('\npassword: This field is required.', '', 1)
                print(text, file=sys.stderr)
                sys.exit(TUTUM_AUTH_ERROR_EXIT_CODE)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def build(tag, working_directory, quiet, no_cache):
    try:
        directory = abspath(working_directory)
        dockerfile_path = join(directory, "Dockerfile")

        if not isfile(dockerfile_path):
            procfile_path = join(directory, "Procfile")
            ports = ""
            process = ''
            if isfile(procfile_path):
                cmd = ['"/start"']
                with open(procfile_path) as procfile:
                    datamap = yaml.load(procfile)
                if len(datamap) > 1:
                    while not process or (not process in datamap):
                        process = raw_input("Process type to build, %s: " % datamap.keys())
                    process = '"%s"' % process

                if (len(datamap) == 1 and 'web' in datamap) or (process == 'web'):
                    ports = "80"
                    process = '"web"'

                cmd.append(process)

            else:
                while not process:
                    process = raw_input("Run command: ")
                cmd = process

            if process != '"web"':
                port_regexp = re.compile('^\d{1,5}(\s\d{1,5})*$')
                while not ports or not bool(port_regexp.match(ports)):
                    ports = raw_input("Exposed Ports (ports separated by whitespace) i.e. 80 8000: ") or ""

            utils.build_dockerfile(dockerfile_path, ports, cmd)

        docker_client = utils.get_docker_client()
        stream = docker_client.build(path=directory, tag=tag, quiet=quiet, nocache=no_cache, rm=True, stream=True)
        try:
            utils.stream_output(stream, sys.stdout)
        except Exception as e:
            print(e.message, file=sys.stderr)
        print(tag)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def service_inspect(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            print(json.dumps(tutum.Service.fetch(service.uuid).get_all_attributes(), indent=2))
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_logs(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            print(service.logs)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_ps(quiet=False, status=None):
    try:
        headers = ["NAME", "UUID", "STATUS", "IMAGE", "DEPLOYED"]
        service_list = tutum.Service.list(state=status)
        data_list = []
        long_uuid_list = []
        for service in service_list:
            data_list.append([service.unique_name, service.uuid[:8],
                              utils.add_unicode_symbol_to_state(service.state),
                              service.image_name,
                              utils.get_humanize_local_datetime_from_utc_datetime_string(service.deployed_datetime)])
            long_uuid_list.append(service.uuid)
        if len(data_list) == 0:
            data_list.append(["", "", "", "", ""])

        if quiet:
            for uuid in long_uuid_list:
                print(uuid)
        else:
            utils.tabulate_result(data_list, headers)

    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def service_redeploy(identifiers, tag):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            result = service.redeploy(tag)
            if result:
                print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_create(image, name, cpu_shares, memory, privileged, target_num_containers, run_command, entrypoint,
                   expose, publish, envvars, tag, linked_to_service, autorestart, autodestroy, roles, sequential):
    try:
        ports = utils.parse_published_ports(publish)

        # Add exposed_port to ports, excluding whose inner_port that has been defined in published ports
        exposed_ports = utils.parse_exposed_ports(expose)
        for exposed_port in exposed_ports:
            existed = False
            for port in ports:
                if exposed_port.get('inner_port', '') == port.get('inner_port', ''):
                    existed = True
                    break
            if not existed:
                ports.append(exposed_port)

        envvars = utils.parse_envvars(envvars)
        links_service = utils.parse_links(linked_to_service, 'to_service')
        service = tutum.Service.create(image=image, name=name, cpu_shares=cpu_shares,
                                       memory=memory, privileged=privileged,
                                       target_num_containers=target_num_containers, run_command=run_command,
                                       entrypoint=entrypoint, container_ports=ports, container_envvars=envvars,
                                       linked_to_service=links_service,
                                       autorestart=autorestart, autodestroy=autodestroy,
                                       roles=roles, sequential_deployment=sequential)
        result = service.save()
        if tag:
            result = service.tag.add(tag)
        if result:
            print(service.uuid)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def service_run(image, name, cpu_shares, memory, privileged, target_num_containers, run_command, entrypoint,
                expose, publish, envvars, tag, linked_to_service, autorestart, autodestroy, roles, sequential):
    try:
        ports = utils.parse_published_ports(publish)

        # Add exposed_port to ports, excluding whose inner_port that has been defined in published ports
        exposed_ports = utils.parse_exposed_ports(expose)
        for exposed_port in exposed_ports:
            existed = False
            for port in ports:
                if exposed_port.get('inner_port', '') == port.get('inner_port', ''):
                    existed = True
                    break
            if not existed:
                ports.append(exposed_port)

        envvars = utils.parse_envvars(envvars)
        links_service = utils.parse_links(linked_to_service, 'to_service')
        service = tutum.Service.create(image=image, name=name, cpu_shares=cpu_shares,
                                       memory=memory, privileged=privileged,
                                       target_num_containers=target_num_containers, run_command=run_command,
                                       entrypoint=entrypoint, container_ports=ports, container_envvars=envvars,
                                       linked_to_service=links_service,
                                       autorestart=autorestart, autodestroy=autodestroy,
                                       roles=roles, sequential_deployment=sequential)
        service.save()
        if tag:
            service.tag.add(tag)
        result = service.start()
        if result:
            print(service.uuid)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def service_scale(identifiers, target_num_containers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            service.target_num_containers = target_num_containers
            result = service.save()
            if result:
                print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_set(autorestart, autodestroy, identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service_details = utils.fetch_remote_service(identifier, raise_exceptions=True)
            if service_details is not None:
                service_details.autorestart = autorestart
                service_details.autodestroy = autodestroy
                result = service_details.save()
                if result:
                    print(service_details.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_start(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            result = service.start()
            if result:
                print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_stop(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            result = service.stop()
            if result:
                print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def service_terminate(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            result = service.delete()
            if result:
                print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def container_inspect(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            container = utils.fetch_remote_container(identifier)
            print(json.dumps(tutum.Container.fetch(container.uuid).get_all_attributes(), indent=2))
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def container_logs(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            container = utils.fetch_remote_container(identifier)
            print(container.logs)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def container_ps(identifier, quiet=False, status=None):
    try:
        headers = ["NAME", "UUID", "STATUS", "IMAGE", "RUN COMMAND", "EXIT CODE", "DEPLOYED", "PORTS"]

        if identifier is None:
            containers = tutum.Container.list(state=status)
        elif utils.is_uuid4(identifier):
            containers = tutum.Container.list(uuid=identifier, state=status)
        else:
            containers = tutum.Container.list(unique_name=identifier, state=status) + \
                         tutum.Container.list(uuid__startswith=identifier, state=status)

        data_list = []
        long_uuid_list = []

        for container in containers:
            ports = []
            for index, port in enumerate(container.container_ports):
                ports_string = ""
                if port['outer_port'] is not None:
                    ports_string += "%s:%d->" % (container.public_dns, port['outer_port'])
                ports_string += "%d/%s" % (port['inner_port'], port['protocol'])
                ports.append(ports_string)

            ports_string = ", ".join(ports)
            data_list.append([container.unique_name,
                              container.uuid[:8],
                              utils.add_unicode_symbol_to_state(container.state),
                              container.image_name,
                              container.run_command,
                              container.exit_code,
                              utils.get_humanize_local_datetime_from_utc_datetime_string(container.deployed_datetime),
                              ports_string])
            long_uuid_list.append(container.uuid)
        if len(data_list) == 0:
            data_list.append(["", "", "", "", "", "", "", ""])
        if quiet:
            for uuid in long_uuid_list:
                print(uuid)
        else:
            utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def container_start(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            container = utils.fetch_remote_container(identifier)
            result = container.start()
            if result:
                print(container.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def container_stop(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            container = utils.fetch_remote_container(identifier)
            result = container.stop()
            if result:
                print(container.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def container_terminate(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            container = utils.fetch_remote_container(identifier)
            result = container.delete()
            if result:
                print(container.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def image_list(quiet=False, jumpstarts=False, linux=False):
    try:
        headers = ["NAME", "DESCRIPTION"]
        data_list = []
        name_list = []
        if jumpstarts:
            image_list = tutum.Image.list(starred=True)
        elif linux:
            image_list = tutum.Image.list(base_image=True)
        else:
            image_list = tutum.Image.list(is_private_image=True)
        if len(image_list) != 0:
            for image in image_list:
                data_list.append([image.name, image.description])
                name_list.append(image.name)
        else:
            data_list.append(["", ""])

        if quiet:
            for name in name_list:
                print(name)
        else:
            utils.tabulate_result(data_list, headers)

    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def image_register(repository, description):
    print('Please input username and password of the repository:')
    username = raw_input('Username: ')
    password = getpass.getpass()
    try:
        image = tutum.Image.create(name=repository, username=username, password=password, description=description)
        result = image.save()
        if result:
            print(image.name)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def image_push(name, public):
    def push_to_public(repository):
        print('Pushing %s to public registry ...' % repository)

        output_status = NO_ERROR
        # tag a image to its name to check if the images exists
        try:
            docker_client.tag(name, name)
        except Exception as e:
            print(e, file=sys.stderr)
            sys.exit(EXCEPTION_EXIT_CODE)
        try:
            stream = docker_client.push(repository, stream=True)
            utils.stream_output(stream, sys.stdout)
        except StreamOutputError as e:
            if 'status 401' in e.message.lower():
                output_status = AUTH_ERROR
            else:
                print(e, file=sys.stderr)
                sys.exit(EXCEPTION_EXIT_CODE)
        except Exception as e:
            print(e.message, file=sys.stderr)
            sys.exit(EXCEPTION_EXIT_CODE)

        if output_status == NO_ERROR:
            print('')
            sys.exit()

        if output_status == AUTH_ERROR:
            print('Please login prior to push:')
            username = raw_input('Username: ')
            password = getpass.getpass()
            email = raw_input('Email: ')
            try:
                result = docker_client.login(username, password=password, email=email)
                if isinstance(result, dict):
                    print(result.get('Status', None))
            except Exception as e:
                print(e, file=sys.stderr)
                sys.exit(TUTUM_AUTH_ERROR_EXIT_CODE)
            push_to_public(repository)

    def push_to_tutum(repository):
        print('Pushing %s to Tutum private registry ...' % repository)

        user = tutum.user
        apikey = tutum.apikey
        if user is None or apikey is None:
            print('Not authorized')
            sys.exit(TUTUM_AUTH_ERROR_EXIT_CODE)

        try:
            registry = os.getenv('TUTUM_REGISTRY_URL') or 'https://tutum.co/v1/'
            docker_client.login(user, apikey, registry=registry)
        except Exception as e:
            print(e, file=sys.stderr)
            sys.exit(TUTUM_AUTH_ERROR_EXIT_CODE)

        if repository:
            repository = filter(None, repository.split('/'))[-1]
        tag = None
        if ':' in repository:
            tag = repository.split(':')[-1]
            repository = repository.replace(':%s' % tag, '')
        repository = '%s/%s/%s' % (registry.split('//')[-1].split('/')[0], user, repository)

        if tag:
            print('Tagging %s as %s:%s ...' % (name, repository, tag))
        else:
            print('Tagging %s as %s ...' % (name, repository))

        try:
            docker_client.tag(name, repository, tag=tag)
        except Exception as e:
            print(e, file=sys.stderr)
            sys.exit(EXCEPTION_EXIT_CODE)

        stream = docker_client.push(repository, stream=True)
        try:
            utils.stream_output(stream, sys.stdout)
        except docker.errors.APIError as e:
            print(e.explanation, file=sys.stderr)
            sys.exit(EXCEPTION_EXIT_CODE)
        except Exception as e:
            print(e.message, file=sys.stderr)
            sys.exit(EXCEPTION_EXIT_CODE)
        print('')

    docker_client = utils.get_docker_client()
    if public:
        push_to_public(name)
    else:
        push_to_tutum(name)


def image_rm(repositories):
    has_exception = False
    for repository in repositories:
        try:
            image = tutum.Image.fetch(repository)
            result = image.delete()
            if result:
                print(repository)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def image_search(text):
    try:
        docker_client = utils.get_docker_client()
        results = docker_client.search(text)
        headers = ["NAME", "DESCRIPTION", "STARS", "OFFICIAL", "TRUSTED"]
        data_list = []
        if len(results) != 0:
            for result in results:
                description = result["description"].replace("\n", "\\n")
                description = description[:80] + " [...]" if len(result["description"]) > 80 else description
                data_list.append([result["name"], description, str(result["star_count"]),
                                  u"\u2713" if result["is_official"] else "",
                                  u"\u2713" if result["is_trusted"] else ""])
        else:
            data_list.append(["", "", "", "", ""])
        utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def image_update(repositories, username, password, description):
    has_exception = False
    for repository in repositories:
        try:
            image = tutum.Image.fetch(repository)
            if username is not None:
                image.username = username
            if password is not None:
                image.password = password
            if description is not None:
                image.description = description
            result = image.save()
            if result:
                print(image.name)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def node_list(quiet=False):
    try:
        headers = ["UUID", "FQDN", "LASTSEEN", "STATUS", "CLUSTER", "DOCKER_VER"]
        node_list = tutum.Node.list()
        data_list = []
        long_uuid_list = []
        for node in node_list:
            cluster_name = node.node_cluster
            try:
                cluster_name = tutum.NodeCluster.fetch(node.node_cluster.strip("/").split("/")[-1]).name
            except:
                pass

            data_list.append([node.uuid[:8],
                              node.external_fqdn,
                              utils.get_humanize_local_datetime_from_utc_datetime_string(node.last_seen),
                              utils.add_unicode_symbol_to_state(node.state),
                              cluster_name, node.docker_version])
            long_uuid_list.append(node.uuid)
        if len(data_list) == 0:
            data_list.append(["", "", "", "", "", ""])
        if quiet:
            for uuid in long_uuid_list:
                print(uuid)
        else:
            utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def node_inspect(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            node = utils.fetch_remote_node(identifier)
            print(json.dumps(tutum.Node.fetch(node.uuid).get_all_attributes(), indent=2))
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def node_rm(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            node = utils.fetch_remote_node(identifier)
            result = node.delete()
            if result:
                print(node.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def node_upgrade(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            node = utils.fetch_remote_node(identifier)
            result = node.upgrade_docker()
            if result:
                print(node.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_list(quiet):
    try:
        headers = ["NAME", "UUID", "REGION", "TYPE", "DEPLOYED", "STATUS", "CURRENT#NODES", "TARGET#NODES"]
        nodecluster_list = tutum.NodeCluster.list()
        data_list = []
        long_uuid_list = []
        for nodecluster in nodecluster_list:
            if quiet:
                long_uuid_list.append(nodecluster.uuid)
                continue

            node_type = nodecluster.node_type
            region = nodecluster.region
            try:
                node_type = tutum.NodeType.fetch(nodecluster.node_type.strip("/").split("api/v1/nodetype/")[-1]).label
                region = tutum.Region.fetch(nodecluster.region.strip("/").split("api/v1/region/")[-1]).label
            except Exception as e:
                pass

            data_list.append([nodecluster.name,
                              nodecluster.uuid[:8],
                              region,
                              node_type,
                              utils.get_humanize_local_datetime_from_utc_datetime_string(nodecluster.deployed_datetime),
                              nodecluster.state,
                              nodecluster.current_num_nodes,
                              nodecluster.target_num_nodes])
            long_uuid_list.append(nodecluster.uuid)
        if len(data_list) == 0:
            data_list.append(["", "", "", "", "", "", "", ""])
        if quiet:
            for uuid in long_uuid_list:
                print(uuid)
        else:
            utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_inspect(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            nodecluster = utils.fetch_remote_nodecluster(identifier)
            print(json.dumps(tutum.NodeCluster.fetch(nodecluster.uuid).get_all_attributes(), indent=2))
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_show_providers(quiet):
    try:
        headers = ["NAME", "LABEL", "REGIONS"]
        data_list = []
        name_list = []
        provider_list = tutum.Provider.list()
        for provider in provider_list:
            if quiet:
                name_list.append(provider.name)
                continue

            data_list.append([provider.name, provider.label,
                              ", ".join([region.strip("/").split("/")[-1] for region in provider.regions])])

        if len(data_list) == 0:
            data_list.append(["", "", ""])
        if quiet:
            for name in name_list:
                print(name)
        else:
            utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_show_regions(provider):
    try:
        headers = ["NAME", "LABEL", "PROVIDER", "TYPE"]
        data_list = []
        region_list = tutum.Region.list()
        for region in region_list:
            provider_name = region.resource_uri.strip("/").split("/")[-2]
            if provider and provider != provider_name:
                continue
            data_list.append([region.name, region.label, provider_name,
                              ", ".join([nodetype.strip("/").split("/")[-1] for nodetype in region.node_types])])

        if len(data_list) == 0:
            data_list.append(["", "", "", ""])
        utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_show_types(provider, region):
    try:
        headers = ["NAME", "LABEL", "PROVIDER", "REGIONS"]
        data_list = []
        nodetype_list = tutum.NodeType.list()
        for nodetype in nodetype_list:
            provider_name = nodetype.resource_uri.strip("/").split("/")[-2]
            regions = [region_uri.strip("/").split("/")[-1] for region_uri in nodetype.regions]
            if provider and provider != provider_name:
                continue

            if region and region not in regions:
                continue
            data_list.append([nodetype.name, nodetype.label, provider_name,
                              ", ".join(regions)])

        if len(data_list) == 0:
            data_list.append(["", "", "", ""])
        utils.tabulate_result(data_list, headers)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_create(target_num_nodes, name, provider, region, nodetype):
    region_uri = "/api/v1/region/%s/%s/" % (provider, region)
    nodetype_uri = "/api/v1/nodetype/%s/%s/" % (provider, nodetype)

    try:
        nodecluster = tutum.NodeCluster.create(name=name, target_num_nodes=target_num_nodes,
                                               region=region_uri, node_type=nodetype_uri)
        nodecluster.save()
        result = nodecluster.deploy()
        if result:
            print(nodecluster.uuid)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_rm(identifiers):
    has_exception = False
    for identifier in identifiers:
        try:
            nodecluster = utils.fetch_remote_nodecluster(identifier)
            result = nodecluster.delete()
            if result:
                print(nodecluster.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def nodecluster_scale(identifiers, target_num_nodes):
    has_exception = False
    for identifier in identifiers:
        try:
            nodecluster = utils.fetch_remote_nodecluster(identifier)
            nodecluster.target_num_nodes = target_num_nodes
            result = nodecluster.save()
            if result:
                print(nodecluster.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def tag_add(identifiers, tags):
    has_exception = False
    for identifier in identifiers:
        try:
            try:
                obj = utils.fetch_remote_service(identifier)
            except ObjectNotFound:
                try:
                    obj = utils.fetch_remote_nodecluster(identifier)
                except ObjectNotFound:
                    try:
                        obj = utils.fetch_remote_node(identifier)
                    except ObjectNotFound:
                        raise ObjectNotFound(
                            "Identifier '%s' does not match any service, node or nodecluster" % identifier)

            tag = tutum.Tag.fetch(obj)
            tag.add(tags)
            tag.save()
            print(obj.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def tag_list(identifiers, quiet):
    has_exception = False

    headers = ["IDENTIFIER", "TYPE", "TAGS"]
    data_list = []
    tags_list = []
    for identifier in identifiers:
        try:
            obj = utils.fetch_remote_service(identifier, raise_exceptions=False)
            if isinstance(obj, ObjectNotFound):
                obj = utils.fetch_remote_nodecluster(identifier, raise_exceptions=False)
                if isinstance(obj, ObjectNotFound):
                    obj = utils.fetch_remote_node(identifier, raise_exceptions=False)
                    if isinstance(obj, ObjectNotFound):
                        raise ObjectNotFound(
                            "Identifier '%s' does not match any service, node or nodecluster" % identifier)
                    else:
                        obj_type = 'Node'
                else:
                    obj_type = 'NodeCluster'
            else:
                obj_type = 'Service'

            tagnames = []
            for tags in tutum.Tag.fetch(obj).list():
                tagname = tags.get('name', '')
                if tagname:
                    tagnames.append(tagname)

            data_list.append([identifier, obj_type, ' '.join(tagnames)])
            tags_list.append(' '.join(tagnames))
        except Exception as e:
            if isinstance(e, ObjectNotFound):
                data_list.append([identifier, 'None', ''])
            else:
                data_list.append([identifier, '', ''])
            tags_list.append('')
            print(e, file=sys.stderr)
            has_exception = True
    if quiet:
        for tags in tags_list:
            print(tags)
    else:
        utils.tabulate_result(data_list, headers)
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def tag_rm(identifiers, tags):
    has_exception = False
    for identifier in identifiers:
        try:
            try:
                obj = utils.fetch_remote_service(identifier)
            except ObjectNotFound:
                try:
                    obj = utils.fetch_remote_nodecluster(identifier)
                except ObjectNotFound:
                    try:
                        obj = utils.fetch_remote_node(identifier)
                    except ObjectNotFound:
                        raise ObjectNotFound(
                            "Identifier '%s' does not match any service, node or nodecluster" % identifier)

            tag = tutum.Tag.fetch(obj)
            for t in tags:
                try:
                    tag.delete(t)
                except Exception as e:
                    print(e, file=sys.stderr)
                    has_exception = True
            print(obj.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def tag_set(identifiers, tags):
    has_exception = False
    for identifier in identifiers:
        try:
            try:
                obj = utils.fetch_remote_service(identifier)
            except ObjectNotFound:
                try:
                    obj = utils.fetch_remote_nodecluster(identifier)
                except ObjectNotFound:
                    try:
                        obj = utils.fetch_remote_node(identifier)
                    except ObjectNotFound:
                        raise ObjectNotFound(
                            "Identifier '%s' does not match any service, node or nodecluster" % identifier)

            obj.tags = []
            for t in tags:
                new_tag = {"name": t}
                if new_tag not in obj.tags:
                    obj.tags.append(new_tag)
            obj.save()

            print(obj.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def webhookhandler_create(identifiers, names):
    has_exception = False
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            webhookhandler = tutum.WebhookHandler.fetch(service)
            webhookhandler.add(names)
            webhookhandler.save()
            print(service.uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)

def webhookhandler_list(identifiers, quiet):
    has_exception = False

    headers = ["IDENTIFIER", "NAME", "UUID"]
    data_list = []
    uuid_list = []
    for identifier in identifiers:
        try:
            service = utils.fetch_remote_service(identifier)
            webhookhandler = tutum.WebhookHandler.fetch(service)
            handlers = webhookhandler.list()
            for handler in handlers:
                data_list.append([identifier, handler.get('name', ''), handler.get('uuid', '')])
                uuid_list.append(handler.get('uuid', ''))
        except Exception as e:
            print(e, file=sys.stderr)
            data_list.append([identifier, '', ''])
            uuid_list.append('')
            has_exception = True
    if quiet:
        for uuid in uuid_list:
            print(uuid)
    else:
        utils.tabulate_result(data_list, headers)
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)


def webhookhandler_rm(identifier, webhook_identifiers):
    has_exception = False
    try:
        service = utils.fetch_remote_service(identifier)
        webhookhandler = tutum.WebhookHandler.fetch(service)
        uuid_list = utils.get_uuids_of_webhookhandler(webhookhandler, webhook_identifiers)
        try:
            for uuid in uuid_list:
                webhookhandler.delete(uuid)
                print (uuid)
        except Exception as e:
            print(e, file=sys.stderr)
            has_exception = True
    except Exception as e:
        print(e, file=sys.stderr)
        has_exception = True
    if has_exception:
        sys.exit(EXCEPTION_EXIT_CODE)

import logging
import json
import time
import io
from unittest import mock, skip

from django.test import TestCase, TransactionTestCase, tag
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings
from rest_framework import status

from celery.contrib.testing.worker import start_worker

from core.celery import app as celery_app
from core.celery import task_routes
from core.swiftmanager import SwiftManager
from plugins.models import PluginMeta, Plugin, PluginParameter, ComputeResource
from plugininstances.models import PluginInstance, PluginInstanceFile
from plugininstances.models import PathParameter, FloatParameter
from plugininstances.services.manager import PluginInstanceManager
from plugininstances import views


COMPUTE_RESOURCE_URL = settings.COMPUTE_RESOURCE_URL


class ViewTests(TestCase):
    
    def setUp(self):
        # avoid cluttered console output (for instance logging all the http requests)
        logging.disable(logging.WARNING)

        self.chris_username = 'chris'
        self.chris_password = 'chris12'
        self.username = 'foo'
        self.password = 'bar'
        self.other_username = 'boo'
        self.other_password = 'far'

        self.content_type='application/vnd.collection+json'

        (self.compute_resource, tf) = ComputeResource.objects.get_or_create(
            name="host", compute_url=COMPUTE_RESOURCE_URL)

        # create the chris superuser and two additional users
        User.objects.create_user(username=self.chris_username,
                                 password=self.chris_password)
        User.objects.create_user(username=self.other_username,
                                 password=self.other_password)
        User.objects.create_user(username=self.username,
                                 password=self.password)
        
        # create two plugins
        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='pacspull', type='fs')
        (plugin_fs, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin_fs.compute_resources.set([self.compute_resource])
        plugin_fs.save()

        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='mri_convert', type='ds')
        (plugin_ds, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin_ds.compute_resources.set([self.compute_resource])
        plugin_ds.save()

    def tearDown(self):
        # re-enable logging
        logging.disable(logging.NOTSET)


class TasksViewTests(TransactionTestCase):

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.WARNING)
        super().setUpClass()
        # route tasks to this worker by using the default 'celery' queue
        # that is exclusively used for the automated tests
        celery_app.conf.update(task_routes=None)
        cls.celery_worker = start_worker(celery_app,
                                         concurrency=1,
                                         perform_ping_check=False)
        cls.celery_worker.__enter__()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.celery_worker.__exit__(None, None, None)
        # reset routes to the original queues
        celery_app.conf.update(task_routes=task_routes)
        logging.disable(logging.NOTSET)

    def setUp(self):

        self.swift_manager = SwiftManager(settings.SWIFT_CONTAINER_NAME,
                                          settings.SWIFT_CONNECTION_PARAMS)
        self.chris_username = 'chris'
        self.chris_password = 'chris12'
        self.username = 'foo'
        self.password = 'bar'
        self.other_username = 'boo'
        self.other_password = 'far'
        self.content_type = 'application/vnd.collection+json'

        (self.compute_resource, tf) = ComputeResource.objects.get_or_create(
            name="host", compute_url=COMPUTE_RESOURCE_URL)

        # create the chris superuser and two additional users
        User.objects.create_user(username=self.chris_username,
                                 password=self.chris_password)
        User.objects.create_user(username=self.other_username,
                                 password=self.other_password)
        user = User.objects.create_user(username=self.username,
                                        password=self.password)

        # create two plugins
        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='pacspull', type='fs')
        (plugin_fs, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin_fs.compute_resources.set([self.compute_resource])
        plugin_fs.save()

        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='mri_convert', type='ds')
        (plugin_ds, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin_ds.compute_resources.set([self.compute_resource])
        plugin_ds.save()

        # create pacspull fs plugin instance
        (self.pl_inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin_fs, owner=user,
            compute_resource=plugin_fs.compute_resources.all()[0])

        # create mri_convert ds plugin instance
        PluginInstance.objects.get_or_create(
            plugin=plugin_ds, owner=user, previous=self.pl_inst,
            compute_resource=plugin_ds.compute_resources.all()[0])


class PluginInstanceListViewTests(TasksViewTests):
    """
    Test the plugininstance-list view.
    """

    def setUp(self):
        super(PluginInstanceListViewTests, self).setUp()
        plugin = Plugin.objects.get(meta__name="pacspull")
        self.create_read_url = reverse("plugininstance-list", kwargs={"pk": plugin.id})
        self.user_space_path = '%s/uploads/' % self.username
        self.post = json.dumps(
            {"template": {"data": [{"name": "dir", "value": self.user_space_path},
                                   {"name": "title", "value": 'test1'}]}})

    def test_plugin_instance_create_success(self):
        # add parameters to the plugin before the POST request
        plugin = Plugin.objects.get(meta__name="pacspull")
        PluginParameter.objects.get_or_create(plugin=plugin, name='dir', type='string',
                                              optional=False)

        # first test 'fs' plugin instance (has no previous plugin instance)

        with mock.patch.object(views.run_plugin_instance, 'delay',
                               return_value=None) as delay_mock:
            # make API request
            self.client.login(username=self.username, password=self.password)
            response = self.client.post(self.create_read_url, data=self.post,
                                        content_type=self.content_type)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # check that the run_plugin_instance task was called with appropriate args
            delay_mock.assert_called_with(response.data['id'])
            self.assertEqual(response.data['status'], 'scheduled')

        # now test 'ds' plugin instance (has previous plugin instance)

        previous_plg_inst = PluginInstance.objects.get(title='test1')
        plugin = Plugin.objects.get(meta__name="mri_convert")
        create_read_url = reverse("plugininstance-list", kwargs={"pk": plugin.id})
        post = json.dumps(
            {"template": {"data": [{"name": "previous_id", "value": previous_plg_inst.id}]}})

        previous_plg_inst.status = 'finishedSuccessfully'
        previous_plg_inst.save()
        with mock.patch.object(views.run_plugin_instance, 'delay',
                               return_value=None) as delay_mock:
            self.client.login(username=self.username, password=self.password)
            response = self.client.post(create_read_url, data=post,
                                        content_type=self.content_type)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # check that the run_plugin_instance task was called with appropriate args
            delay_mock.assert_called_with(response.data['id'])
            self.assertEqual(response.data['status'], 'scheduled')

        previous_plg_inst.status = 'started'
        previous_plg_inst.save()
        with mock.patch.object(views.run_plugin_instance, 'delay',
                               return_value=None) as delay_mock:
            self.client.login(username=self.username, password=self.password)
            response = self.client.post(create_read_url, data=post,
                                        content_type=self.content_type)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # check that the run_plugin_instance task was not called
            delay_mock.assert_not_called()
            self.assertEqual(response.data['status'], 'waitingForPrevious')

        previous_plg_inst.status = 'finishedWithError'
        previous_plg_inst.save()
        with mock.patch.object(views.run_plugin_instance, 'delay',
                               return_value=None) as delay_mock:
            self.client.login(username=self.username, password=self.password)
            response = self.client.post(create_read_url, data=post,
                                        content_type=self.content_type)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # check that the run_plugin_instance task was not called
            delay_mock.assert_not_called()
            self.assertEqual(response.data['status'], 'cancelled')

    @tag('integration')
    def test_integration_plugin_instance_create_success(self):

        # add an FS plugin to the system
        plugin_parameters = [{'name': 'dir', 'type': 'path', 'action': 'store',
                              'optional': False, 'flag': '--dir', 'short_flag': '-d',
                              'help': 'test plugin', 'ui_exposed': True}]

        self.plg_data = {'description': 'A simple chris fs app demo',
                         'version': '0.1',
                         'dock_image': 'fnndsc/pl-simplefsapp',
                         'execshell': 'python3',
                         'selfpath': '/usr/src/simplefsapp',
                         'selfexec': 'simplefsapp.py'}

        self.plg_meta_data = {'name': 'simplefsapp',
                              'title': 'Dir plugin',
                              'license': 'MIT',
                              'type': 'fs',
                              'icon': 'http://github.com/plugin',
                              'category': 'Dir',
                              'stars': 0,
                              'authors': 'FNNDSC (dev@babyMRI.org)'}

        self.plugin_repr = self.plg_data.copy()
        self.plugin_repr.update(self.plg_meta_data)
        self.plugin_repr['parameters'] = plugin_parameters

        (compute_resource, tf) = ComputeResource.objects.get_or_create(
            name="host", compute_url=COMPUTE_RESOURCE_URL)

        data = self.plg_meta_data.copy()
        (pl_meta, tf) = PluginMeta.objects.get_or_create(**data)
        data = self.plg_data.copy()
        (plugin, tf) = Plugin.objects.get_or_create(meta=pl_meta, **data)
        plugin.compute_resources.set([compute_resource])
        plugin.save()

        # add plugin's parameters
        parameters = plugin_parameters
        PluginParameter.objects.get_or_create(
            plugin=plugin,
            name=parameters[0]['name'],
            type=parameters[0]['type'],
            flag=parameters[0]['flag'])

        # upload a file to the Swift storage user's space
        with io.StringIO('Test file') as f:
            self.swift_manager.upload_obj(self.user_space_path + 'test.txt', f.read(),
                                          content_type='text/plain')

        # make POST API request to create a plugin instance
        create_read_url = reverse("plugininstance-list", kwargs={"pk": plugin.id})
        self.client.login(username=self.username, password=self.password)
        response = self.client.post(create_read_url, data=self.post,
                                    content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # delete files from swift storage
        self.swift_manager.delete_obj(self.user_space_path + 'test.txt')

    def test_plugin_instance_create_failure_unauthenticated(self):
        response = self.client.post(self.create_read_url, data=self.post,
                                    content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plugin_instance_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.create_read_url)
        self.assertContains(response, "pacspull")

    def test_plugin_instance_list_failure_unauthenticated(self):
        response = self.client.get(self.create_read_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PluginInstanceDetailViewTests(TasksViewTests):
    """
    Test the plugininstance-detail view.
    """

    def setUp(self):
        super(PluginInstanceDetailViewTests, self).setUp()

        self.read_update_delete_url = reverse("plugininstance-detail",
                                              kwargs={"pk": self.pl_inst.id})

    def test_plugin_instance_detail_success(self):
        self.pl_inst.status = 'started'
        self.pl_inst.save()
        with mock.patch.object(views.check_plugin_instance_exec_status, 'delay',
                               return_value=None) as delay_mock:
            # make API request
            self.client.login(username=self.username, password=self.password)
            response = self.client.get(self.read_update_delete_url)
            self.assertContains(response, "pacspull")
            self.assertEqual(response.data['status'], 'started')
            # check that the check_plugin_instance_exec_status task was called with appropriate args
            delay_mock.assert_called_with(self.pl_inst.id)

    @tag('integration', 'error-pman')
    def test_integration_plugin_instance_detail_success(self):

        # add an FS plugin to the system
        plugin_parameters = [{'name': 'dir', 'type': 'path', 'action': 'store',
                              'optional': False, 'flag': '--dir', 'short_flag': '-d',
                              'help': 'test plugin', 'ui_exposed': True}]

        self.plg_data = {'description': 'A simple chris fs app demo',
                         'version': '0.1',
                         'dock_image': 'fnndsc/pl-simplefsapp',
                         'execshell': 'python3',
                         'selfpath': '/usr/src/simplefsapp',
                         'selfexec': 'simplefsapp.py'}

        self.plg_meta_data = {'name': 'simplefsapp',
                              'title': 'Dir plugin',
                              'license': 'MIT',
                              'type': 'fs',
                              'icon': 'http://github.com/plugin',
                              'category': 'Dir',
                              'stars': 0,
                              'authors': 'FNNDSC (dev@babyMRI.org)'}

        self.plugin_repr = self.plg_data.copy()
        self.plugin_repr.update(self.plg_meta_data)
        self.plugin_repr['parameters'] = plugin_parameters

        (compute_resource, tf) = ComputeResource.objects.get_or_create(
            name="host", compute_url=COMPUTE_RESOURCE_URL)

        data = self.plg_meta_data.copy()
        (pl_meta, tf) = PluginMeta.objects.get_or_create(**data)
        data = self.plg_data.copy()
        (plugin, tf) = Plugin.objects.get_or_create(meta=pl_meta, **data)
        plugin.compute_resources.set([compute_resource])
        plugin.save()

        # add plugin's parameters
        parameters = plugin_parameters
        (pl_param, tf) = PluginParameter.objects.get_or_create(
            plugin=plugin,
            name=parameters[0]['name'],
            type=parameters[0]['type'],
            flag=parameters[0]['flag'])

        # upload a file to the Swift storage user's space
        user_space_path = '%s/uploads/' % self.username
        with io.StringIO('Test file') as f:
            self.swift_manager.upload_obj(user_space_path + 'test.txt', f.read(),
                                          content_type='text/plain')

        # create a plugin's instance
        user = User.objects.get(username=self.username)
        (pl_inst, tf) = PluginInstance.objects.get_or_create(
            title='test2', plugin=plugin,
            owner=user, compute_resource=plugin.compute_resources.all()[0])
        pl_inst.status = 'scheduled'
        pl_inst.save()
        PathParameter.objects.get_or_create(plugin_inst=pl_inst, plugin_param=pl_param,
                                            value=user_space_path)
        read_update_delete_url = reverse("plugininstance-detail",
                                         kwargs={"pk": pl_inst.id})

        # run the plugin instance
        plg_inst_manager = PluginInstanceManager(pl_inst)
        plg_inst_manager.run_plugin_instance_app()

        # make API GET request
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(read_update_delete_url)
        self.assertContains(response, "simplefsapp")
        self.assertContains(response, 'started')

        # In the following we keep checking the status until the job ends with
        # 'finishedSuccessfully'. The code runs in a lazy loop poll with a
        # max number of attempts at 10 second intervals.
        maxLoopTries = 20
        currentLoop = 1
        b_checkAgain = True
        time.sleep(10)
        while b_checkAgain:
            response = self.client.get(read_update_delete_url)
            str_responseStatus = response.data['status']
            if str_responseStatus == 'finishedSuccessfully':
                b_checkAgain = False
            elif currentLoop < maxLoopTries:
                time.sleep(10)
            if currentLoop == maxLoopTries:
                b_checkAgain = False
            currentLoop += 1
        self.assertContains(response, "finishedSuccessfully")

        # delete files from swift storage
        self.swift_manager.delete_obj(user_space_path + 'test.txt')
        # obj_paths = self.swift_manager.ls(pl_inst.get_output_path())
        # for path in obj_paths:
        #     self.swift_manager.delete_obj(path)

    def test_plugin_instance_detail_failure_unauthenticated(self):
        response = self.client.get(self.read_update_delete_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plugin_instance_update_success(self):
        put = json.dumps({
            "template": {"data": [{"name": "title", "value": "Test instance"},
                                  {"name": "status", "value": "cancelled"}]}})

        self.client.login(username=self.username, password=self.password)
        response = self.client.put(self.read_update_delete_url, data=put,
                                   content_type=self.content_type)
        self.assertContains(response, "Test instance")
        self.assertContains(response, "cancelled")

    def test_plugin_instance_update_failure_current_status_is_finishedSuccessfully_or_finishedWithError(self):
        put = json.dumps({
            "template": {"data": [{"name": "status", "value": "cancelled"}]}})

        self.pl_inst.status = 'finishedSuccessfully'
        self.pl_inst.save()
        self.client.login(username=self.username, password=self.password)
        response = self.client.put(self.read_update_delete_url, data=put,
                                   content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.pl_inst.status = 'finishedWithError'
        self.pl_inst.save()
        self.client.login(username=self.username, password=self.password)
        response = self.client.put(self.read_update_delete_url, data=put,
                                   content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plugin_instance_update_failure_status_can_only_be_changed_to_cancelled(self):
        put = json.dumps({
            "template": {"data": [{"name": "status", "value": "finishedSuccessfully"}]}})

        self.client.login(username=self.username, password=self.password)
        response = self.client.put(self.read_update_delete_url, data=put,
                                   content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plugin_instance_update_failure_unauthenticated(self):
        response = self.client.put(self.read_update_delete_url, data={},
                                   content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plugin_instance_update_failure_access_denied(self):
        put = json.dumps({
            "template": {"data": [{"name": "status", "value": "cancelled"}]}})

        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.put(self.read_update_delete_url, data=put,
                                   content_type=self.content_type)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_plugin_instance_delete_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.delete(self.read_update_delete_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(PluginInstance.objects.count(), 0)

    def test_plugin_instance_delete_failure_unauthenticated(self):
        response = self.client.delete(self.read_update_delete_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plugin_instance_delete_failure_access_denied(self):
        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.delete(self.read_update_delete_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PluginInstanceListQuerySearchViewTests(ViewTests):
    """
    Test the plugininstance-list-query-search view.
    """

    def setUp(self):
        super(PluginInstanceListQuerySearchViewTests, self).setUp()

        user = User.objects.get(username=self.username)
        
        # create two plugin instances
        plugin = Plugin.objects.get(meta__name="pacspull")
        (inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, compute_resource=plugin.compute_resources.all()[0])

        plugin = Plugin.objects.get(meta__name="mri_convert")
        (inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, previous=inst,
            compute_resource=plugin.compute_resources.all()[0])
        # set second instance's status
        inst.status = 'finishedSuccessfully'
        inst.save()

        self.list_url = reverse("allplugininstance-list-query-search") + '?status=created'

    def test_plugin_instance_query_search_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        # response should only contain the instances that match the query
        self.assertContains(response, 'created')
        self.assertNotContains(response,'finishedSuccessfully')

    def test_plugin_instance_query_search_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PluginInstanceDescendantListViewTests(ViewTests):
    """
    Test the plugininstance-descendant-list view.
    """

    def setUp(self):
        super(PluginInstanceDescendantListViewTests, self).setUp()

        user = User.objects.get(username=self.username)

        # create an 'fs' plugin instance
        plugin = Plugin.objects.get(meta__name="pacspull")
        (fs_inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, compute_resource=plugin.compute_resources.all()[0])

        # create a tree of 'ds' plugin instances
        plugin = Plugin.objects.get(meta__name="mri_convert")
        PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, previous=fs_inst,
            compute_resource=plugin.compute_resources.all()[0])

        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='mri_info', type='ds')
        (plugin, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin.compute_resources.set([self.compute_resource])
        plugin.save()
        (ds_inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, previous=fs_inst,
            compute_resource=plugin.compute_resources.all()[0])

        (pl_meta, tf) = PluginMeta.objects.get_or_create(name='mri_surf', type='ds')
        (plugin, tf) = Plugin.objects.get_or_create(meta=pl_meta, version='0.1')
        plugin.compute_resources.set([self.compute_resource])
        plugin.save()
        PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, previous=ds_inst,
            compute_resource=plugin.compute_resources.all()[0])

        self.list_url = reverse("plugininstance-descendant-list", kwargs={"pk": fs_inst.id})

    def test_plugin_instance_descendant_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        # response should contain all the instances in the tree
        self.assertContains(response, "pacspull")
        self.assertContains(response, "mri_convert")
        self.assertContains(response, "mri_info")
        self.assertContains(response, "mri_surf")

    def test_plugin_instance_descendant_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PluginInstanceParameterListViewTests(ViewTests):
    """
    Test the plugininstance-parameter-list view.
    """

    def setUp(self):
        super(PluginInstanceParameterListViewTests, self).setUp()

        user = User.objects.get(username=self.username)

        # create a plugin
        plugin = Plugin.objects.get(meta__name="pacspull")
        parameters = [{"type": "path", "name": "param1", "flag": "--param1"},
                      {"type": "float", "name": "param2", "flag": "--param2"}]

        # add plugin's parameters
        (param1, tf) = PluginParameter.objects.get_or_create(
            plugin=plugin,
            name=parameters[0]['name'],
            type=parameters[0]['type'],
            flag=parameters[0]['flag'])
        (param2, tf) = PluginParameter.objects.get_or_create(
            plugin=plugin,
            name=parameters[1]['name'],
            type=parameters[1]['type'],
            flag=parameters[1]['flag'])

        # create a plugin instance
        (inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, compute_resource=plugin.compute_resources.all()[0])

        # create two plugin parameter instances associated to the plugin instance
        PathParameter.objects.get_or_create(plugin_inst=inst, plugin_param=param1,
                                            value=self.username)
        FloatParameter.objects.get_or_create(plugin_inst=inst, plugin_param=param2,
                                             value=3.14)

        self.list_url = reverse("plugininstance-parameter-list", kwargs={"pk": inst.id})

    def test_plugin_instance_parameter_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        self.assertContains(response, "param1")
        self.assertContains(response, self.username)
        self.assertContains(response, "param2")
        self.assertContains(response, 3.14)

    def test_plugin_instance_parameter_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PluginInstanceFileViewTests(ViewTests):
    """
    Generic plugin instance file view tests' setup and tearDown.
    """

    def setUp(self):
        super(PluginInstanceFileViewTests, self).setUp()
        # create a plugin instance
        user = User.objects.get(username=self.username)
        plugin = Plugin.objects.get(meta__name="pacspull")
        (self.plg_inst, tf) = PluginInstance.objects.get_or_create(
            plugin=plugin, owner=user, compute_resource=plugin.compute_resources.all()[0])
        # create test directory where files are created
        # self.test_dir = settings.MEDIA_ROOT + '/test'
        # settings.MEDIA_ROOT = self.test_dir
        # if not os.path.exists(self.test_dir):
        #     os.makedirs(self.test_dir)

    def tearDown(self):
        super(PluginInstanceFileViewTests, self).tearDown()
        # remove test directory
        # shutil.rmtree(self.test_dir)
        # settings.MEDIA_ROOT = os.path.dirname(self.test_dir)


class PluginInstanceFileListViewTests(PluginInstanceFileViewTests):
    """
    Test the plugininstancefile-list view.
    """

    def setUp(self):
        super(PluginInstanceFileListViewTests, self).setUp()

        # create a plugin instance file associated to the plugin instance
        plg_inst = self.plg_inst
        (plg_inst_file, tf) = PluginInstanceFile.objects.get_or_create(plugin_inst=plg_inst)
        plg_inst_file.fname.name = 'test_file.txt'
        plg_inst_file.save()

        self.list_url = reverse("plugininstancefile-list", kwargs={"pk": plg_inst.id})

    def test_plugin_instance_file_create_failure_post_not_allowed(self):
        self.client.login(username=self.username, password=self.password)
        # try to create a new plugin file with a POST request to the list
        # POST request using multipart/form-data to be able to upload file
        with io.StringIO("test file") as f:
            post = {"fname": f}
            response = self.client.post(self.list_url, data=post)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_plugin_instance_file_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        self.assertContains(response, "test_file.txt")

    def test_plugin_instance_file_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plugin_instance_file_list_failure_access_denied(self):
        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AllPluginInstanceFileListViewTests(PluginInstanceFileViewTests):
    """
    Test the allplugininstancefile-list view.
    """

    def setUp(self):
        super(AllPluginInstanceFileListViewTests, self).setUp()

        # create a plugin instance file associated to the plugin instance
        plg_inst = self.plg_inst
        (plg_inst_file, tf) = PluginInstanceFile.objects.get_or_create(plugin_inst=plg_inst)
        plg_inst_file.fname.name = 'test_file.txt'
        plg_inst_file.save()

        self.list_url = reverse("allplugininstancefile-list")

    def test_all_plugin_instance_file_create_failure_post_not_allowed(self):
        self.client.login(username=self.username, password=self.password)
        # try to create a new plugin file with a POST request to the list
        # POST request using multipart/form-data to be able to upload file
        with io.StringIO("test file") as f:
            post = {"fname": f}
            response = self.client.post(self.list_url, data=post)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_all_plugin_instance_file_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        self.assertContains(response, "test_file.txt")

    def test_all_plugin_instance_file_list_from_shared_feed_success(self):
        self.client.login(username=self.other_username, password=self.other_password)
        plg_inst = self.plg_inst
        user1 = User.objects.get(username=self.username)
        user2 = User.objects.get(username=self.other_username)
        plg_inst.feed.owner.set([user1, user2])
        response = self.client.get(self.list_url)
        self.assertContains(response, "test_file.txt")

    def test_all_plugin_instance_file_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_all_plugin_instance_file_list_files_in_not_owned_feeds_inaccessible(self):
        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.get(self.list_url)
        self.assertNotContains(response, "test_file.txt")


class AllPluginInstanceFileListQuerySearchViewTests(PluginInstanceFileViewTests):
    """
    Test the allplugininstancefile-list-query-search view.
    """

    def setUp(self):
        super(AllPluginInstanceFileListQuerySearchViewTests, self).setUp()

        # create a plugin instance file associated to the plugin instance
        plg_inst = self.plg_inst
        (plg_inst_file, tf) = PluginInstanceFile.objects.get_or_create(plugin_inst=plg_inst)
        plg_inst_file.fname.name = 'test_file.txt'
        plg_inst_file.save()

        self.list_url = reverse("allplugininstancefile-list-query-search") + '?id=' + \
                        str(plg_inst_file.id)

    def test_plugin_instance_query_search_list_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.list_url)
        self.assertContains(response, 'test_file.txt')

    def test_plugin_instance_query_search_list_failure_unauthenticated(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PluginInstanceFileDetailViewTests(PluginInstanceFileViewTests):
    """
    Test the plugininstancefile-detail view.
    """

    def setUp(self):
        super(PluginInstanceFileDetailViewTests, self).setUp()
        #self.corresponding_feed_url = reverse("feed-detail", kwargs={"pk": feed.id})
        plg_inst = self.plg_inst
        self.corresponding_plugin_instance_url = reverse("plugininstance-detail",
                                                         kwargs={"pk": plg_inst.id})

        # create a file in the DB "already uploaded" to the server
        (plg_inst_file, tf) = PluginInstanceFile.objects.get_or_create(plugin_inst=plg_inst)
        plg_inst_file.fname.name = 'file1.txt'
        plg_inst_file.save()

        self.read_url = reverse("plugininstancefile-detail",
                                kwargs={"pk": plg_inst_file.id})

    def test_plugin_instance_file_detail_success(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.read_url)
        self.assertContains(response, "file1.txt")
        self.assertTrue(response.data["plugin_inst"].endswith(
            self.corresponding_plugin_instance_url))

    def test_plugin_instance_file_detail_success_user_chris(self):
        self.client.login(username=self.chris_username, password=self.chris_password)
        response = self.client.get(self.read_url)
        self.assertContains(response, "file1.txt")
        self.assertTrue(response.data["plugin_inst"].endswith(
            self.corresponding_plugin_instance_url))

    def test_plugin_instance_file_detail_failure_not_related_feed_owner(self):
        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.get(self.read_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_plugin_instance_file_detail_failure_unauthenticated(self):
        response = self.client.get(self.read_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class FileResourceViewTests(PluginInstanceFileViewTests):
    """
    Test the plugininstancefile-resource view.
    """

    def setUp(self):
        super(FileResourceViewTests, self).setUp()
        plg_inst = self.plg_inst
        # create a file in the DB "already uploaded" to the server
        (plg_inst_file, tf) = PluginInstanceFile.objects.get_or_create(
            plugin_inst=plg_inst)
        plg_inst_file.fname.name = '/tests/file1.txt'
        plg_inst_file.save()
        self.download_url = reverse("plugininstancefile-resource",
                                    kwargs={"pk": plg_inst_file.id}) + 'file1.txt'

    def test_fileresource_get(self):
        plg_inst_file = PluginInstanceFile.objects.get(fname="/tests/file1.txt")
        fileresource_view_inst = mock.Mock()
        fileresource_view_inst.get_object = mock.Mock(return_value=plg_inst_file)
        request_mock = mock.Mock()
        with mock.patch('plugininstances.views.Response') as response_mock:
            views.FileResource.get(fileresource_view_inst, request_mock)
            response_mock.assert_called_with(plg_inst_file.fname)

    @tag('integration')
    def test_integration_fileresource_download_success(self):
        swift_manager = SwiftManager(settings.SWIFT_CONTAINER_NAME,
                                     settings.SWIFT_CONNECTION_PARAMS)
        # upload file to Swift storage
        with io.StringIO("test file") as file1:
            swift_manager.upload_obj('/tests/file1.txt', file1.read(),
                                      content_type='text/plain')

        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.content, 'utf-8'), "test file")

        # delete file from Swift storage
        swift_manager.delete_obj('/tests/file1.txt')

    def test_fileresource_download_failure_not_related_feed_owner(self):
        self.client.login(username=self.other_username, password=self.other_password)
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_fileresource_download_failure_unauthenticated(self):
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

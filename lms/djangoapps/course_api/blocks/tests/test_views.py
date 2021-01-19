"""
Tests for Blocks Views
"""


from datetime import datetime
from unittest import mock
from unittest.mock import Mock
from urllib.parse import urlencode, urlunparse

from django.conf import settings
from django.urls import reverse
from opaque_keys.edx.locator import CourseLocator
from completion.test_utils import CompletionWaffleTestMixin, submit_completions_for_testing

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import ToyCourseFactory

from .helpers import deserialize_usage_key


class TestBlocksView(SharedModuleStoreTestCase):
    """
    Test class for BlocksView
    """
    requested_fields = ['graded', 'format', 'student_view_multi_device', 'children', 'not_a_field', 'due']
    BLOCK_TYPES_WITH_STUDENT_VIEW_DATA = ['video', 'discussion', 'html']

    @classmethod
    def setUpClass(cls):
        super(TestBlocksView, cls).setUpClass()

        # create a toy course
        cls.course = ToyCourseFactory.create(
            modulestore=cls.store,
            due=datetime(3013, 9, 18, 11, 30, 00),
        )
        cls.course_key = cls.course.id
        cls.course_usage_key = cls.store.make_course_usage_key(cls.course_key)

        cls.non_orphaned_block_usage_keys = set(
            str(item.location)
            for item in cls.store.get_items(cls.course_key)
            # remove all orphaned items in the course, except for the root 'course' block
            if cls.store.get_parent_location(item.location) or item.category == 'course'
        )

    def setUp(self):
        super(TestBlocksView, self).setUp()

        # create and enroll user in the toy course
        self.user = UserFactory.create()
        self.admin_user = AdminFactory.create()
        self.client.login(username=self.user.username, password='test')
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_key)

        # default values for url and query_params
        self.url = reverse(
            'blocks_in_block_tree',
            kwargs={'usage_key_string': str(self.course_usage_key)}
        )
        self.query_params = {'depth': 'all', 'username': self.user.username}

    def verify_response(self, expected_status_code=200, params=None, url=None, cacheable=False):
        """
        Ensure that sending a GET request to the specified URL returns the
        expected status code.

        Arguments:
            expected_status_code: The status_code that is expected in the
                response.
            params: Parameters to add to self.query_params to include in the
                request.
            url: The URL to send the GET request.  Default is self.url.

        Returns:
            response: The HttpResponse returned by the request
        """
        if params:
            self.query_params.update(params)
        response = self.client.get(url or self.url, self.query_params)
        self.assertEqual(response.status_code, expected_status_code, str(response.content))
        if cacheable:
            assert response.get('Cache-Control', None) == 'max-age={}'.format(
                settings.CACHE_MIDDLEWARE_SECONDS
            )
        else:
            assert response.get('Cache-Control', None) is None
        return response

    def verify_response_block_list(self, response):
        """
        Verify that the response contains only the expected block ids.
        """
        self.assertSetEqual(
            {block['id'] for block in response.data},
            self.non_orphaned_block_usage_keys,
        )

    def verify_response_block_dict(self, response):
        """
        Verify that the response contains the expected blocks
        """
        self.assertSetEqual(
            set(response.data['blocks'].keys()),
            self.non_orphaned_block_usage_keys,
        )

    def verify_response_with_requested_fields(self, response):
        """
        Verify the response has the expected structure
        """
        self.verify_response_block_dict(response)
        for block_key_string, block_data in response.data['blocks'].items():
            block_key = deserialize_usage_key(block_key_string, self.course_key)
            xblock = self.store.get_item(block_key)

            self.assert_in_iff('children', block_data, xblock.has_children)
            self.assert_in_iff('graded', block_data, xblock.graded is not None)
            self.assert_in_iff('format', block_data, xblock.format is not None)
            self.assert_in_iff('due', block_data, xblock.due is not None)
            self.assert_true_iff(block_data['student_view_multi_device'], block_data['type'] == 'html')
            self.assertNotIn('not_a_field', block_data)

            if xblock.has_children:
                self.assertSetEqual(
                    set(str(child.location) for child in xblock.get_children()),
                    set(block_data['children']),
                )

    def assert_in_iff(self, member, container, predicate):
        """
        Assert that member is in container if and only if predicate is true.

        Arguments:
            member - any object
            container - any container
            predicate - an expression, tested for truthiness
        """
        if predicate:
            self.assertIn(member, container)
        else:
            self.assertNotIn(member, container)

    def assert_true_iff(self, expression, predicate):
        """
        Assert that the expression is true if and only if the predicate is true

        Arguments:
            expression
            predicate
        """

        if predicate:
            self.assertTrue(expression)
        else:
            self.assertFalse(expression)

    def test_not_authenticated_non_public_course_with_other_username(self):
        """
        Verify behaviour when accessing course blocks of a non-public course for another user anonymously.
        """
        self.client.logout()
        self.verify_response(403)

    def test_not_authenticated_non_public_course_with_all_blocks(self):
        """
        Verify behaviour when accessing all course blocks of a non-public course anonymously.
        """
        self.client.logout()
        self.query_params.pop('username')
        self.query_params['all_blocks'] = True
        self.verify_response(403)

    def test_not_authenticated_non_public_course_with_blank_username(self):
        """
        Verify behaviour when accessing course blocks of a non-public course for anonymous user anonymously.
        """
        self.client.logout()
        self.query_params['username'] = ''
        self.verify_response(403)

    @mock.patch("lms.djangoapps.course_api.blocks.forms.permissions.is_course_public", Mock(return_value=True))
    def test_not_authenticated_public_course_with_other_username(self):
        """
        Verify behaviour when accessing course blocks of a public course for another user anonymously.
        """
        self.client.logout()
        self.verify_response(403)

    @mock.patch("lms.djangoapps.course_api.blocks.forms.permissions.is_course_public", Mock(return_value=True))
    def test_not_authenticated_public_course_with_all_blocks(self):
        """
        Verify behaviour when accessing all course blocks of a public course anonymously.
        """
        self.client.logout()
        self.query_params.pop('username')
        self.query_params['all_blocks'] = True
        self.verify_response(403)

    @mock.patch("lms.djangoapps.course_api.blocks.forms.permissions.is_course_public", Mock(return_value=True))
    def test_not_authenticated_public_course_with_blank_username(self):
        """
        Verify behaviour when accessing course blocks of a public course for anonymous user anonymously.
        """
        self.client.logout()
        self.query_params['username'] = ''
        self.verify_response(cacheable=True)

    def test_not_enrolled_non_public_course(self):
        """
        Verify behaviour when accessing course blocks for a non-public course as a user not enrolled in course.
        """
        CourseEnrollment.unenroll(self.user, self.course_key)
        self.verify_response(403)

    @mock.patch("lms.djangoapps.course_api.blocks.forms.permissions.is_course_public", Mock(return_value=True))
    def test_not_enrolled_public_course(self):
        """
        Verify behaviour when accessing course blocks for a public course as a user not enrolled in course.
        """
        self.query_params['username'] = ''
        CourseEnrollment.unenroll(self.user, self.course_key)
        self.verify_response(cacheable=True)

    @mock.patch("lms.djangoapps.course_api.blocks.forms.permissions.is_course_public", Mock(return_value=True))
    def test_public_course_all_blocks_and_empty_username(self):
        """
        Verify behaviour when specifying both all_blocks and username='', and ensure the response is not cached.
        """
        self.query_params['username'] = ''
        self.query_params['all_blocks'] = True
        # Verify response for a regular user.
        self.verify_response(403, cacheable=False)
        # Verify response for an unenrolled user.
        CourseEnrollment.unenroll(self.user, self.course_key)
        self.verify_response(403, cacheable=False)
        # Verify response for an anonymous user.
        self.client.logout()
        self.verify_response(403, cacheable=False)
        # Verify response for a staff user.
        self.client.login(username=self.admin_user.username, password='test')
        self.verify_response(cacheable=False)

    def test_non_existent_course(self):
        usage_key = self.store.make_course_usage_key(CourseLocator('non', 'existent', 'course'))
        url = reverse(
            'blocks_in_block_tree',
            kwargs={'usage_key_string': str(usage_key)}
        )
        self.verify_response(403, url=url)

    def test_no_user_non_staff(self):
        self.query_params.pop('username')
        self.query_params['all_blocks'] = True
        self.verify_response(403)

    def test_no_user_staff_not_all_blocks(self):
        self.query_params.pop('username')
        self.verify_response(400)

    def test_no_user_staff_all_blocks(self):
        self.client.login(username=self.admin_user.username, password='test')
        self.query_params.pop('username')
        self.query_params['all_blocks'] = True
        self.verify_response()

    def test_basic(self):
        response = self.verify_response()
        self.assertEqual(response.data['root'], str(self.course_usage_key))
        self.verify_response_block_dict(response)
        for block_key_string, block_data in response.data['blocks'].items():
            block_key = deserialize_usage_key(block_key_string, self.course_key)
            self.assertEqual(block_data['id'], block_key_string)
            self.assertEqual(block_data['type'], block_key.block_type)
            self.assertEqual(block_data['display_name'], self.store.get_item(block_key).display_name or '')

    def test_return_type_param(self):
        response = self.verify_response(params={'return_type': 'list'})
        self.verify_response_block_list(response)

    def test_block_counts_param(self):
        response = self.verify_response(params={'block_counts': ['course', 'chapter']})
        self.verify_response_block_dict(response)
        for block_data in response.data['blocks'].values():
            self.assertEqual(
                block_data['block_counts']['course'],
                1 if block_data['type'] == 'course' else 0,
            )
            self.assertEqual(
                block_data['block_counts']['chapter'],
                (
                    1 if block_data['type'] == 'chapter' else
                    5 if block_data['type'] == 'course' else
                    0
                )
            )

    def test_student_view_data_param(self):
        response = self.verify_response(params={
            'student_view_data': self.BLOCK_TYPES_WITH_STUDENT_VIEW_DATA + ['chapter']
        })
        self.verify_response_block_dict(response)
        for block_data in response.data['blocks'].values():
            self.assert_in_iff(
                'student_view_data',
                block_data,
                block_data['type'] in self.BLOCK_TYPES_WITH_STUDENT_VIEW_DATA
            )

    def test_extra_field_when_requested(self):
        """
        Tests if all requested extra fields appear in output

        Requests the fields specified under "COURSE_BLOCKS_API_EXTRA_FIELDS"
        in the Test Django settings

        Test setting "COURSE_BLOCKS_API_EXTRA_FIELDS" contains:
            - other_course_settings
            - course_visibility
        """
        self.client.login(username=self.admin_user.username, password='test')
        response = self.verify_response(params={
            'all_blocks': True,
            'requested_fields': ['other_course_settings', 'course_visibility'],
        })
        self.verify_response_block_dict(response)
        for block_data in response.data['blocks'].values():
            self.assert_in_iff(
                'other_course_settings',
                block_data,
                block_data['type'] == 'course'
            )

            self.assert_in_iff(
                'course_visibility',
                block_data,
                block_data['type'] == 'course'
            )

    def test_extra_field_when_not_requested(self):
        """
        Tests if fields that weren't requested would appear in output

        Requests some of the fields specified under
        "COURSE_BLOCKS_API_EXTRA_FIELDS" in the Test Django settings
        The other extra fields specified in Test Django settings weren't
        requested to see if they would show up in the output or not

        Test setting "COURSE_BLOCKS_API_EXTRA_FIELDS" contains:
            - other_course_settings
            - course_visibility
        """
        self.client.login(username=self.admin_user.username, password='test')
        response = self.verify_response(params={
            'all_blocks': True,
            'requested_fields': ['course_visibility'],
        })
        self.verify_response_block_dict(response)
        for block_data in response.data['blocks'].values():
            self.assertNotIn(
                'other_course_settings',
                block_data
            )

            self.assert_in_iff(
                'course_visibility',
                block_data,
                block_data['type'] == 'course'
            )

    def test_navigation_param(self):
        response = self.verify_response(params={'nav_depth': 10})
        self.verify_response_block_dict(response)
        for block_data in response.data['blocks'].values():
            self.assertIn('descendants', block_data)

    def test_requested_fields_param(self):
        response = self.verify_response(
            params={'requested_fields': self.requested_fields}
        )
        self.verify_response_with_requested_fields(response)

    def test_with_list_field_url(self):
        query = urlencode(list(self.query_params.items()) + [
            ('requested_fields', self.requested_fields[0]),
            ('requested_fields', self.requested_fields[1]),
            ('requested_fields', ",".join(self.requested_fields[1:])),
        ])
        self.query_params = None
        response = self.verify_response(
            url=urlunparse(("", "", self.url, "", query, ""))
        )
        self.verify_response_with_requested_fields(response)


class TestBlocksInCourseView(TestBlocksView):  # pylint: disable=test-inherits-tests
    """
    Test class for BlocksInCourseView
    """

    def setUp(self):
        super(TestBlocksInCourseView, self).setUp()
        self.url = reverse('blocks_in_course')
        self.query_params['course_id'] = str(self.course_key)

    def test_no_course_id(self):
        self.query_params.pop('course_id')
        self.verify_response(400)

    def test_invalid_course_id(self):
        self.verify_response(400, params={'course_id': 'invalid_course_id'})

    def test_non_existent_course(self):
        self.verify_response(403, params={'course_id': str(CourseLocator('non', 'existent', 'course'))})

    def test_non_existent_course_anonymous(self):
        self.client.logout()
        self.query_params['username'] = ''
        self.verify_response(403, params={'course_id': str(CourseLocator('non', 'existent', 'course'))})


class TestBlocksWithCompletionView(TestBlocksInCourseView,  # pylint: disable=test-inherits-tests
                                   CompletionWaffleTestMixin):
    """
    Test class for BlocksInCourseView
    """

    def setUp(self):
        super().setUp()
        self.override_waffle_switch(True)
        self.url = reverse('blocks_with_completion_in_course')
        self.query_params['course_id'] = str(self.course_key)
        self.non_orphaned_raw_block_usage_keys = set(
            item.location
            for item in self.store.get_items(self.course_key)
            # remove all orphaned items in the course, except for the root 'course' block
            if self.store.get_parent_location(item.location) or item.category == 'course'
        )

    def test_completion_one_unit(self):
        for item in self.store.get_items(self.course_key):
            if item.category == 'html':
                block_usage_key = item.location
                break

        submit_completions_for_testing(self.user, [block_usage_key])
        response = self.verify_response(params={
            'depth': 'all',
            'requested_fields': ['completion', 'children'],
        })

        completion = response.data['blocks'][str(block_usage_key)].get('completion')
        self.assert_true_iff(completion, 1)

    def test_completion_all_course(self):
        for block in self.non_orphaned_raw_block_usage_keys:
            submit_completions_for_testing(self.user, [block])

        response = self.verify_response(params={
            'depth': 'all',
            'requested_fields': ['completion', 'children'],
        })
        for block_id in self.non_orphaned_block_usage_keys:
            self.assert_true_iff(response.data['blocks'][block_id].get('completion'), 1)

    def test_completion_all_course_with_list_return_type(self):
        for block in self.non_orphaned_raw_block_usage_keys:
            submit_completions_for_testing(self.user, [block])

        response = self.verify_response(params={
            'depth': 'all',
            'return_type': 'list',
            'requested_fields': ['completion', 'children'],
        })
        for block in response.data:
            if block['block_id'] in self.non_orphaned_block_usage_keys:
                self.assert_true_iff(block.get('completion'), 1)

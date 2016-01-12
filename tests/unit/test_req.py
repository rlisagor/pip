import os
import shutil
import sys
import tempfile

import pytest

from mock import Mock, patch, mock_open
from pip.commands.install import InstallCommand
from pip.exceptions import (PreviousBuildDirError, InvalidWheelFilename,
                            InstallationError, UnsupportedWheel, HashErrors)
from pip.download import path_to_url, PipSession
from pip.index import PackageFinder, Link
from pip.req import (InstallRequirement, RequirementSet, Requirements)
from pip.req.req_file import process_line
from pip.req.req_install import parse_req
from pip.utils import read_text_file
from pip._vendor import pkg_resources
from tests.lib import assert_raises_regexp, requirements_file


class TestRequirementSet(object):
    """RequirementSet tests"""

    def setup(self):
        self.tempdir = tempfile.mkdtemp()

    def teardown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def basic_reqset(self, **kwargs):
        return RequirementSet(
            build_dir=os.path.join(self.tempdir, 'build'),
            src_dir=os.path.join(self.tempdir, 'src'),
            download_dir=None,
            session=PipSession(),
            **kwargs
        )

    def test_no_reuse_existing_build_dir(self, data):
        """Test prepare_files raise exception with previous build dir"""

        build_dir = os.path.join(self.tempdir, 'build', 'simple')
        os.makedirs(build_dir)
        open(os.path.join(build_dir, "setup.py"), 'w')
        reqset = self.basic_reqset()
        req = InstallRequirement.from_line('simple')
        reqset.add_requirement(req)
        finder = PackageFinder([data.find_links], [], session=PipSession())
        assert_raises_regexp(
            PreviousBuildDirError,
            "pip can't proceed with [\s\S]*%s[\s\S]*%s" %
            (req, build_dir.replace('\\', '\\\\')),
            reqset.prepare_files,
            finder,
        )

    def test_environment_marker_extras(self, data):
        """
        Test that the environment marker extras are used with
        non-wheel installs.
        """
        reqset = self.basic_reqset()
        req = InstallRequirement.from_editable(
            data.packages.join("LocalEnvironMarker"))
        reqset.add_requirement(req)
        finder = PackageFinder([data.find_links], [], session=PipSession())
        reqset.prepare_files(finder)
        # This is hacky but does test both case in py2 and py3
        if sys.version_info[:2] in ((2, 7), (3, 4)):
            assert reqset.has_requirement('simple')
        else:
            assert not reqset.has_requirement('simple')

    @pytest.mark.network
    def test_missing_hash_checking(self, data):
        """Make sure prepare_files() raises an error when a requirement has no
        hash in implicit hash-checking mode.
        """
        reqset = self.basic_reqset()
        # No flags here. This tests that detection of later flags nonetheless
        # requires earlier packages to have hashes:
        reqset.add_requirement(
            list(process_line('blessings==1.0', 'file', 1))[0])
        # This flag activates --require-hashes mode:
        reqset.add_requirement(
            list(process_line('tracefront==0.1 --hash=sha256:somehash',
                              'file',
                              2))[0])
        # This hash should be accepted because it came from the reqs file, not
        # from the internet:
        reqset.add_requirement(
            list(process_line('https://pypi.python.org/packages/source/m/more-'
                              'itertools/more-itertools-1.0.tar.gz#md5=b21850c'
                              '3cfa7efbb70fd662ab5413bdd', 'file', 3))[0])
        finder = PackageFinder([],
                               ['https://pypi.python.org/simple'],
                               session=PipSession())
        assert_raises_regexp(
            HashErrors,
            r'Hashes are required in --require-hashes mode, but they are '
            r'missing .*\n'
            r'    blessings==1.0 --hash=sha256:[0-9a-f]+\n'
            r'THESE PACKAGES DO NOT MATCH THE HASHES.*\n'
            r'    tracefront==0.1 .*:\n'
            r'        Expected sha256 somehash\n'
            r'             Got        [0-9a-f]+$',
            reqset.prepare_files,
            finder)

    def test_missing_hash_with_require_hashes(self, data):
        """Setting --require-hashes explicitly should raise errors if hashes
        are missing.
        """
        reqset = self.basic_reqset(require_hashes=True)
        reqset.add_requirement(
            list(process_line('simple==1.0', 'file', 1))[0])
        finder = PackageFinder([data.find_links], [], session=PipSession())
        assert_raises_regexp(
            HashErrors,
            r'Hashes are required in --require-hashes mode, but they are '
            r'missing .*\n'
            r'    simple==1.0 --hash=sha256:393043e672415891885c9a2a0929b1af95'
            r'fb866d6ca016b42d2e6ce53619b653$',
            reqset.prepare_files,
            finder)

    def test_missing_hash_with_require_hashes_in_reqs_file(self, data, tmpdir):
        """--require-hashes in a requirements file should make its way to the
        RequirementSet.
        """
        req_set = self.basic_reqset(require_hashes=False)
        session = PipSession()
        finder = PackageFinder([data.find_links], [], session=session)
        command = InstallCommand()
        with requirements_file('--require-hashes', tmpdir) as reqs_file:
            options, args = command.parse_args(['-r', reqs_file])
            command.populate_requirement_set(
                req_set, args, options, finder, session, command.name,
                wheel_cache=None)
        assert req_set.require_hashes

    def test_unsupported_hashes(self, data):
        """VCS and dir links should raise errors when --require-hashes is
        on.

        In addition, complaints about the type of requirement (VCS or dir)
        should trump the presence or absence of a hash.

        """
        reqset = self.basic_reqset(require_hashes=True)
        reqset.add_requirement(
            list(process_line(
                'git+git://github.com/pypa/pip-test-package --hash=sha256:123',
                'file',
                1))[0])
        dir_path = data.packages.join('FSPkg')
        reqset.add_requirement(
            list(process_line(
                'file://%s' % (dir_path,),
                'file',
                2))[0])
        finder = PackageFinder([data.find_links], [], session=PipSession())
        sep = os.path.sep
        if sep == '\\':
            sep = '\\\\'  # This needs to be escaped for the regex
        assert_raises_regexp(
            HashErrors,
            r"Can't verify hashes for these requirements because we don't "
            r"have a way to hash version control repositories:\n"
            r"    git\+git://github\.com/pypa/pip-test-package \(from -r file "
            r"\(line 1\)\)\n"
            r"Can't verify hashes for these file:// requirements because they "
            r"point to directories:\n"
            r"    file://.*{sep}data{sep}packages{sep}FSPkg "
            "\(from -r file \(line 2\)\)".format(sep=sep),
            reqset.prepare_files,
            finder)

    def test_unpinned_hash_checking(self, data):
        """Make sure prepare_files() raises an error when a requirement is not
        version-pinned in hash-checking mode.
        """
        reqset = self.basic_reqset()
        # Test that there must be exactly 1 specifier:
        reqset.add_requirement(
            list(process_line('simple --hash=sha256:a90427ae31f5d1d0d7ec06ee97'
                              'd9fcf2d0fc9a786985250c1c83fd68df5911dd',
                              'file',
                              1))[0])
        # Test that the operator must be ==:
        reqset.add_requirement(list(process_line(
            'simple2>1.0 --hash=sha256:3ad45e1e9aa48b4462af0'
            '123f6a7e44a9115db1ef945d4d92c123dfe21815a06',
            'file',
            2))[0])
        finder = PackageFinder([data.find_links], [], session=PipSession())
        assert_raises_regexp(
            HashErrors,
            # Make sure all failing requirements are listed:
            r'versions pinned with ==. These do not:\n'
            r'    simple .* \(from -r file \(line 1\)\)\n'
            r'    simple2>1.0 .* \(from -r file \(line 2\)\)',
            reqset.prepare_files,
            finder)

    def test_hash_mismatch(self, data):
        """A hash mismatch should raise an error."""
        file_url = path_to_url(
            (data.packages / 'simple-1.0.tar.gz').abspath)
        reqset = self.basic_reqset(require_hashes=True)
        reqset.add_requirement(
            list(process_line('%s --hash=sha256:badbad' % file_url,
                              'file',
                              1))[0])
        finder = PackageFinder([data.find_links], [], session=PipSession())
        assert_raises_regexp(
            HashErrors,
            r'THESE PACKAGES DO NOT MATCH THE HASHES.*\n'
            r'    file:///.*/data/packages/simple-1\.0\.tar\.gz .*:\n'
            r'        Expected sha256 badbad\n'
            r'             Got        393043e672415891885c9a2a0929b1af95fb866d'
            r'6ca016b42d2e6ce53619b653$',
            reqset.prepare_files,
            finder)

    def test_unhashed_deps_on_require_hashes(self, data):
        """Make sure unhashed, unpinned, or otherwise unrepeatable
        dependencies get complained about when --require-hashes is on."""
        reqset = self.basic_reqset()
        finder = PackageFinder([data.find_links], [], session=PipSession())
        reqset.add_requirement(next(process_line(
            'TopoRequires2==0.0.1 '  # requires TopoRequires
            '--hash=sha256:eaf9a01242c9f2f42cf2bd82a6a848cd'
            'e3591d14f7896bdbefcf48543720c970',
            'file', 1)))
        assert_raises_regexp(
            HashErrors,
            r'In --require-hashes mode, all requirements must have their '
            r'versions pinned.*\n'
            r'    TopoRequires from .*$',
            reqset.prepare_files,
            finder)

    def test_hashed_deps_on_require_hashes(self, data):
        """Make sure hashed dependencies get installed when --require-hashes
        is on.

        (We actually just check that no "not all dependencies are hashed!"
        error gets raised while preparing; there is no reason to expect
        installation to then fail, as the code paths are the same as ever.)

        """
        reqset = self.basic_reqset()
        reqset.add_requirement(next(process_line(
            'TopoRequires2==0.0.1 '  # requires TopoRequires
            '--hash=sha256:eaf9a01242c9f2f42cf2bd82a6a848cd'
            'e3591d14f7896bdbefcf48543720c970',
            'file', 1)))
        reqset.add_requirement(next(process_line(
            'TopoRequires==0.0.1 '
            '--hash=sha256:d6dd1e22e60df512fdcf3640ced3039b3b02a56ab2cee81ebcb'
            '3d0a6d4e8bfa6',
            'file', 2)))

    def test_no_egg_on_require_hashes(self, data):
        """Make sure --egg is illegal with --require-hashes.

        --egg would cause dependencies to always be installed, since it cedes
        control directly to setuptools.

        """
        reqset = self.basic_reqset(require_hashes=True, as_egg=True)
        finder = PackageFinder([data.find_links], [], session=PipSession())
        with pytest.raises(InstallationError):
            reqset.prepare_files(finder)


@pytest.mark.parametrize(('file_contents', 'expected'), [
    (b'\xf6\x80', b'\xc3\xb6\xe2\x82\xac'),  # cp1252
    (b'\xc3\xb6\xe2\x82\xac', b'\xc3\xb6\xe2\x82\xac'),  # utf-8
    (b'\xc3\xb6\xe2', b'\xc3\x83\xc2\xb6\xc3\xa2'),  # Garbage
])
def test_egg_info_data(file_contents, expected):
    om = mock_open(read_data=file_contents)
    em = Mock()
    em.return_value = 'cp1252'
    with patch('pip.utils.open', om, create=True):
        with patch('locale.getpreferredencoding', em):
            ret = read_text_file('foo')
    assert ret == expected.decode('utf-8')


class TestInstallRequirement(object):

    def test_url_with_query(self):
        """InstallRequirement should strip the fragment, but not the query."""
        url = 'http://foo.com/?p=bar.git;a=snapshot;h=v0.1;sf=tgz'
        fragment = '#egg=bar'
        req = InstallRequirement.from_line(url + fragment)
        assert req.link.url == url + fragment, req.link

    def test_unsupported_wheel_requirement_raises(self):
        with pytest.raises(UnsupportedWheel):
            InstallRequirement.from_line(
                'peppercorn-0.4-py2.py3-bogus-any.whl',
            )

    def test_installed_version_not_installed(self):
        req = InstallRequirement.from_line('simple-0.1-py2.py3-none-any.whl')
        assert req.installed_version is None

    def test_str(self):
        req = InstallRequirement.from_line('simple==0.1')
        assert str(req) == 'simple==0.1'

    def test_repr(self):
        req = InstallRequirement.from_line('simple==0.1')
        assert repr(req) == (
            '<InstallRequirement object: simple==0.1 editable=False>'
        )

    def test_invalid_wheel_requirement_raises(self):
        with pytest.raises(InvalidWheelFilename):
            InstallRequirement.from_line('invalid.whl')

    def test_wheel_requirement_sets_req_attribute(self):
        req = InstallRequirement.from_line('simple-0.1-py2.py3-none-any.whl')
        assert req.req == pkg_resources.Requirement.parse('simple==0.1')

    def test_url_preserved_line_req(self):
        """Confirm the url is preserved in a non-editable requirement"""
        url = 'git+http://foo.com@ref#egg=foo'
        req = InstallRequirement.from_line(url)
        assert req.link.url == url

    def test_url_preserved_editable_req(self):
        """Confirm the url is preserved in a editable requirement"""
        url = 'git+http://foo.com@ref#egg=foo'
        req = InstallRequirement.from_editable(url)
        assert req.link.url == url

    def test_get_dist(self):
        req = InstallRequirement.from_line('foo')
        req.egg_info_path = Mock(return_value='/path/to/foo.egg-info')
        dist = req.get_dist()
        assert isinstance(dist, pkg_resources.Distribution)
        assert dist.project_name == 'foo'
        assert dist.location == '/path/to'

    def test_get_dist_trailing_slash(self):
        # Tests issue fixed by https://github.com/pypa/pip/pull/2530
        req = InstallRequirement.from_line('foo')
        req.egg_info_path = Mock(return_value='/path/to/foo.egg-info/')
        dist = req.get_dist()
        assert isinstance(dist, pkg_resources.Distribution)
        assert dist.project_name == 'foo'
        assert dist.location == '/path/to'

    def test_markers(self):
        for line in (
            # recommanded syntax
            'mock3; python_version >= "3"',
            # with more spaces
            'mock3 ; python_version >= "3" ',
            # without spaces
            'mock3;python_version >= "3"',
        ):
            req = InstallRequirement.from_line(line)
            assert req.req.project_name == 'mock3'
            assert req.req.specs == []
            assert req.markers == 'python_version >= "3"'

    def test_markers_semicolon(self):
        # check that the markers can contain a semicolon
        req = InstallRequirement.from_line('semicolon; os_name == "a; b"')
        assert req.req.project_name == 'semicolon'
        assert req.req.specs == []
        assert req.markers == 'os_name == "a; b"'

    def test_markers_url(self):
        # test "URL; markers" syntax
        url = 'http://foo.com/?p=bar.git;a=snapshot;h=v0.1;sf=tgz'
        line = '%s; python_version >= "3"' % url
        req = InstallRequirement.from_line(line)
        assert req.link.url == url, req.link
        assert req.markers == 'python_version >= "3"'

        # without space, markers are part of the URL
        url = 'http://foo.com/?p=bar.git;a=snapshot;h=v0.1;sf=tgz'
        line = '%s;python_version >= "3"' % url
        req = InstallRequirement.from_line(line)
        assert req.link.url == line, req.link
        assert req.markers is None

    def test_markers_match(self):
        # match
        for markers in (
            'python_version >= "1.0"',
            'sys_platform == %r' % sys.platform,
        ):
            line = 'name; ' + markers
            req = InstallRequirement.from_line(line)
            assert req.markers == markers
            assert req.match_markers()

        # don't match
        for markers in (
            'python_version >= "5.0"',
            'sys_platform != %r' % sys.platform,
        ):
            line = 'name; ' + markers
            req = InstallRequirement.from_line(line)
            assert req.markers == markers
            assert not req.match_markers()

    def test_extras_for_line_path_requirement(self):
        line = 'SomeProject[ex1,ex2]'
        filename = 'filename'
        comes_from = '-r %s (line %s)' % (filename, 1)
        req = InstallRequirement.from_line(line, comes_from=comes_from)
        assert len(req.extras) == 2
        assert req.extras[0] == 'ex1'
        assert req.extras[1] == 'ex2'

    def test_extras_for_line_url_requirement(self):
        line = 'git+https://url#egg=SomeProject[ex1,ex2]'
        filename = 'filename'
        comes_from = '-r %s (line %s)' % (filename, 1)
        req = InstallRequirement.from_line(line, comes_from=comes_from)
        assert len(req.extras) == 2
        assert req.extras[0] == 'ex1'
        assert req.extras[1] == 'ex2'

    def test_extras_for_editable_path_requirement(self):
        url = '.[ex1,ex2]'
        filename = 'filename'
        comes_from = '-r %s (line %s)' % (filename, 1)
        req = InstallRequirement.from_editable(url, comes_from=comes_from)
        assert len(req.extras) == 2
        assert req.extras[0] == 'ex1'
        assert req.extras[1] == 'ex2'

    def test_extras_for_editable_url_requirement(self):
        url = 'git+https://url#egg=SomeProject[ex1,ex2]'
        filename = 'filename'
        comes_from = '-r %s (line %s)' % (filename, 1)
        req = InstallRequirement.from_editable(url, comes_from=comes_from)
        assert len(req.extras) == 2
        assert req.extras[0] == 'ex1'
        assert req.extras[1] == 'ex2'

    def test_unexisting_path(self):
        with pytest.raises(InstallationError) as e:
            InstallRequirement.from_line(
                os.path.join('this', 'path', 'does', 'not', 'exist'))
        err_msg = e.value.args[0]
        assert "Invalid requirement" in err_msg
        assert "It looks like a path. Does it exist ?" in err_msg

    def test_single_equal_sign(self):
        with pytest.raises(InstallationError) as e:
            InstallRequirement.from_line('toto=42')
        err_msg = e.value.args[0]
        assert "Invalid requirement" in err_msg
        assert "= is not a valid operator. Did you mean == ?" in err_msg

    def test_traceback(self):
        with pytest.raises(InstallationError) as e:
            InstallRequirement.from_line('toto 42')
        err_msg = e.value.args[0]
        assert "Invalid requirement" in err_msg
        assert "\nTraceback " in err_msg


def test_requirements_data_structure_keeps_order():
    requirements = Requirements()
    requirements['pip'] = 'pip'
    requirements['nose'] = 'nose'
    requirements['coverage'] = 'coverage'

    assert ['pip', 'nose', 'coverage'] == list(requirements.values())
    assert ['pip', 'nose', 'coverage'] == list(requirements.keys())


def test_requirements_data_structure_implements__repr__():
    requirements = Requirements()
    requirements['pip'] = 'pip'
    requirements['nose'] = 'nose'

    assert "Requirements({'pip': 'pip', 'nose': 'nose'})" == repr(requirements)


def test_requirements_data_structure_implements__contains__():
    requirements = Requirements()
    requirements['pip'] = 'pip'

    assert 'pip' in requirements
    assert 'nose' not in requirements


@patch('os.path.normcase')
@patch('pip.req.req_install.os.getcwd')
@patch('pip.req.req_install.os.path.exists')
@patch('pip.req.req_install.os.path.isdir')
@patch('pip.req.req_install.is_installable_dir')
def test_parse_editable_local(
        isdir_mock, exists_mock, getcwd_mock, normcase_mock,
        is_installable_dir_mock):
    exists_mock.return_value = isdir_mock.return_value = True
    is_installable_dir_mock.return_value = True

    # mocks needed to support path operations on windows tests
    normcase_mock.return_value = getcwd_mock.return_value = "/some/path"
    assert parse_req('.', True, 'git') == (None, Link('file:///some/path'),
                                           None, None)
    assert parse_req('./foo', True, 'git') == (None,
                                               Link('file:///some/path/foo'),
                                               None, None)


def test_parse_editable_default_vcs():
    assert parse_req('https://foo#egg=foo', True, 'git') == (
        'foo',
        Link('git+https://foo#egg=foo'),
        None,
        None,
    )


def test_parse_editable_explicit_vcs():
    assert parse_req('svn+https://foo#egg=foo', True, 'git') == (
        'foo',
        Link('svn+https://foo#egg=foo'),
        None,
        None,
    )


def test_parse_editable_vcs_extras():
    assert parse_req('svn+https://foo#egg=foo[extras]', 'git') == (
        'foo[extras]',
        Link('svn+https://foo#egg=foo[extras]'),
        None,
        None,
    )


@patch('os.path.normcase')
@patch('pip.req.req_install.os.getcwd')
@patch('pip.req.req_install.os.path.exists')
@patch('pip.req.req_install.os.path.isdir')
@patch('pip.req.req_install.is_installable_dir')
def test_parse_editable_local_extras(
        isdir_mock, exists_mock, getcwd_mock, normcase_mock,
        installable_dir_mock):
    exists_mock.return_value = isdir_mock.return_value = True
    normcase_mock.return_value = getcwd_mock.return_value = "/some/path"
    installable_dir_mock.return_value = True
    assert parse_req('.[extras]', True, 'git') == (
        None, Link('file://' + "/some/path"), "[extras]", None,
    )
    normcase_mock.return_value = "/some/path"
    assert parse_req('./foo[bar,baz]', True, 'git') == (
        None, Link('file:///some/path/foo'), "[bar,baz]", None,
    )


def test_exclusive_environment_markers():
    """Make sure RequirementSet accepts several excluding env markers"""
    eq26 = InstallRequirement.from_line(
        "Django>=1.6.10,<1.7 ; python_version == '2.6'")
    ne26 = InstallRequirement.from_line(
        "Django>=1.6.10,<1.8 ; python_version != '2.6'")

    req_set = RequirementSet('', '', '', session=PipSession())
    req_set.add_requirement(eq26)
    req_set.add_requirement(ne26)
    assert req_set.has_requirement('Django')

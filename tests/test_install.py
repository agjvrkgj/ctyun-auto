"""Offline deployment checks; package managers and Docker are never run for real."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def shell(self, code, *args, **kwargs):
        return subprocess.run(
            ["bash", "-c", 'source "$1"; shift; ' + code,
             "test", str(PROJECT / "install.sh"), *map(str, args)],
            text=True, capture_output=True, timeout=15, **kwargs,
        )

    def git(self, directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), *args], check=True,
            text=True, capture_output=True,
        ).stdout.strip()

    def repository(self):
        source = self.root / "source"
        source.mkdir()
        self.git(source, "init", "-b", "main")
        self.git(source, "config", "user.name", "Installer Test")
        self.git(source, "config", "user.email", "test@example.invalid")
        (source / "app").mkdir()
        (source / "app/Dockerfile").write_text("FROM scratch\n")
        (source / "deploy.sh").write_text("#!/bin/bash\nexit 0\n")
        self.git(source, "add", ".")
        self.git(source, "commit", "-m", "fixture")
        return source, self.root / "install with spaces"

    def prepare(self, source, target):
        return self.shell('REPO_URL="$1"; INSTALL_DIR="$2"; prepare_repository', source, target)

    def test_help_works_from_file_and_pipe(self):
        for command in (["bash", str(PROJECT / "install.sh"), "--help"],
                        ["bash", "-s", "--", "--help"]):
            result = subprocess.run(
                command, input=(PROJECT / "install.sh").read_text(),
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--cron", result.stdout)

    def test_bad_arguments_fail(self):
        for args in [("--dir",), ("--unknown",), ("--dir", "relative"),
                     ("--dir", "/"), ("--branch", "--bad")]:
            result = self.shell('parse_args "$@"', *args)
            self.assertNotEqual(result.returncode, 0, args)

    def test_cron_selection(self):
        result = self.shell('parse_args --cron; printf "%s" "$DEPLOY_SCRIPT"')
        self.assertEqual(result.stdout, "deploy_cron.sh")

    def test_clone_and_fast_forward_keep_data(self):
        source, target = self.repository()
        self.assertEqual(self.prepare(source, target).returncode, 0)
        self.git(target, "config", "user.name", "Installer Test")
        self.git(target, "config", "user.email", "test@example.invalid")
        (source / ".gitignore").write_text("data/\n")
        self.git(source, "add", ".")
        self.git(source, "commit", "-m", "update")
        result = self.prepare(source, target)
        self.assertEqual(result.returncode, 0, result.stderr)
        (target / "data").mkdir()
        (target / "data/session").write_text("keep me")
        self.assertEqual(self.prepare(source, target).returncode, 0)
        self.assertEqual((target / "data/session").read_text(), "keep me")
        self.assertEqual(self.git(source, "rev-parse", "HEAD"),
                         self.git(target, "rev-parse", "HEAD"))

    def test_dirty_checkout_is_preserved(self):
        source, target = self.repository()
        self.assertEqual(self.prepare(source, target).returncode, 0)
        (target / "deploy.sh").write_text("local edit\n")
        result = self.prepare(source, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("本地修改", result.stderr)
        self.assertEqual((target / "deploy.sh").read_text(), "local edit\n")

    def test_wrong_repository_is_rejected(self):
        source, target = self.repository()
        self.assertEqual(self.prepare(source, target).returncode, 0)
        self.git(target, "remote", "set-url", "origin", "https://example.invalid/other.git")
        result = self.prepare(source, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("其他仓库", result.stderr)

    def test_divergent_history_is_not_reset(self):
        source, target = self.repository()
        self.assertEqual(self.prepare(source, target).returncode, 0)
        self.git(target, "config", "user.name", "Installer Test")
        self.git(target, "config", "user.email", "test@example.invalid")
        for directory, name in [(source, "upstream"), (target, "local")]:
            (directory / name).write_text(name)
            self.git(directory, "add", ".")
            self.git(directory, "commit", "-m", name)
        before = self.git(target, "rev-parse", "HEAD")
        self.assertNotEqual(self.prepare(source, target).returncode, 0)
        self.assertEqual(before, self.git(target, "rev-parse", "HEAD"))

    def test_non_repository_directory_is_preserved(self):
        source, target = self.repository()
        target.mkdir()
        (target / "important").write_text("keep")
        result = self.prepare(source, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / "important").read_text(), "keep")

    def test_apt_dependency_failure_stops_before_install(self):
        result = self.shell('''
            command() { if [[ "$*" == '-v git' ]]; then return 1; fi; builtin command "$@"; }
            apt-get() { printf '%s\\n' "$*"; return 42; }
            install_dependencies
        ''')
        self.assertEqual(result.returncode, 42)
        self.assertIn("update", result.stdout)
        self.assertNotIn("install -y", result.stdout)

    def test_existing_docker_is_reused(self):
        result = self.shell('''
            docker() { return 0; }
            systemctl() { return 0; }
            curl() { echo unexpected_download; return 99; }
            prepare_docker
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unexpected_download", result.stdout)

    def test_docker_download_failure_is_not_executed_and_is_cleaned_up(self):
        installer = self.root / "download"
        result = self.shell('''
            command() { if [[ "$*" == '-v docker' ]]; then return 1; fi; builtin command "$@"; }
            mktemp() { printf '%s' "$TEST_DOWNLOAD"; }
            curl() { echo 'echo should_not_execute' > "$TEST_DOWNLOAD"; return 22; }
            trap cleanup EXIT
            prepare_docker
        ''', env={**os.environ, "TEST_DOWNLOAD": str(installer)})
        self.assertEqual(result.returncode, 22)
        self.assertNotIn("should_not_execute", result.stdout)
        self.assertFalse(installer.exists())

    def test_unavailable_docker_fails(self):
        result = self.shell('''
            docker() { return 1; }
            systemctl() { return 0; }
            service() { return 0; }
            sleep() { :; }
            prepare_docker
        ''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker 服务不可用", result.stderr)

    def deployment(self, *, existing=False, build_fail=False, running=True,
                   mode="build", pull_fail=False, missing_image=False, build_only=False):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'password': os.getenv('APP_PASSWORD')}) + '\\n')
if args[0] == 'build' and os.getenv('TEST_BUILD_FAIL') == '1':
    sys.exit(17)
if args[0] == 'pull' and os.getenv('TEST_PULL_FAIL') == '1':
    sys.exit(18)
if args[:2] == ['image', 'inspect']:
    if os.getenv('TEST_MISSING_IMAGE') == '1':
        sys.exit(19)
    print('sha256:' + 'a' * 64)
if args[0] == 'ps' and os.getenv('TEST_EXISTING') == '1':
    print('existing-container')
if args[0] == 'inspect':
    print(os.environ['TEST_RUNNING'])
''')
        docker.chmod(0o755)
        sleep = bin_dir / "sleep"
        sleep.write_text("#!/bin/bash\nexit 0\n")
        sleep.chmod(0o755)
        logfile = self.root / "docker.jsonl"
        password = "test\\password'\"$literal"
        result = subprocess.run(
            ["bash", str(PROJECT / ("build.sh" if build_only else "deploy.sh"))], cwd=self.root,
            input=f"18100000000\n{password}\n{self.root / 'data dir'}\n" + ("n\n" if existing else "\n"),
            text=True, capture_output=True, timeout=10,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                 "TEST_LOG": str(logfile), "TEST_EXISTING": str(int(existing)),
                 "TEST_BUILD_FAIL": str(int(build_fail)),
                 "CTYUN_IMAGE_MODE": mode,
                 "CTYUN_IMAGE": "",
                 "TEST_PULL_FAIL": str(int(pull_fail)),
                 "TEST_MISSING_IMAGE": str(int(missing_image)),
                 "TEST_RUNNING": str(running).lower()},
        )
        calls = [json.loads(line) for line in logfile.read_text().splitlines()]
        return result, calls, password

    def test_deploy_from_other_directory_preserves_password(self):
        result, calls, password = self.deployment()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = next(call for call in calls if call['args'][0] == 'run')
        self.assertEqual(run['password'], password)
        self.assertNotIn(password, ' '.join(run['args']))
        self.assertNotIn(password, result.stdout + result.stderr)

    def test_declining_replacement_keeps_container(self):
        result, calls, _ = self.deployment(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][0] in ('rm', 'run') for call in calls))

    def test_failed_build_keeps_container(self):
        result, calls, _ = self.deployment(existing=True, build_fail=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][0] in ('rm', 'run') for call in calls))

    def test_stopped_container_is_not_reported_as_success(self):
        result, _, _ = self.deployment(running=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("部署与首次配置完成", result.stdout)

    def test_prebuilt_image_is_pulled_before_account_input(self):
        result, calls, _ = self.deployment(mode="pull")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call['args'][0] == 'pull' for call in calls))
        self.assertFalse(any(call['args'][0] == 'build' for call in calls))
        run = next(call for call in calls if call['args'][0] == 'run')
        self.assertEqual(run['args'][-1], 'sha256:' + 'a' * 64)
        prepare = next(call for call in calls if call['args'][0] == 'pull')
        self.assertIsNone(prepare['password'])

    def test_pull_failure_does_not_start_slow_build_or_replace_container(self):
        result, calls, _ = self.deployment(mode="pull", pull_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call['args'][0] in ('build', 'rm', 'run') for call in calls))

    def test_existing_image_skips_network_and_build(self):
        result, calls, _ = self.deployment(mode="local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][0] in ('build', 'pull') for call in calls))

    def test_missing_local_image_does_not_replace_container(self):
        result, calls, _ = self.deployment(mode="local", missing_image=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call['args'][0] in ('build', 'pull', 'rm', 'run') for call in calls))

    def test_build_only_does_not_read_credentials_or_create_container(self):
        result, calls, _ = self.deployment(build_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][0] in ('run', 'rm', 'pull') for call in calls))
        build = next(call for call in calls if call['args'][0] == 'build')
        self.assertNotIn('-q', build['args'])
        self.assertIsNone(build['password'])

    def test_build_only_installer_options(self):
        result = self.shell('parse_args --build-only --image example:v1; printf "%s %s %s" "$BUILD_ONLY" "$CTYUN_IMAGE_MODE" "$CTYUN_IMAGE"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'true build example:v1')


if __name__ == "__main__":
    unittest.main()

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

    def deployment(self, *, existing=False, build_fail=False, running=True):
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
            ["bash", str(PROJECT / "deploy.sh")], cwd=self.root,
            input=f"18100000000\n{password}\n{self.root / 'data dir'}\n" + ("n\n" if existing else "\n"),
            text=True, capture_output=True, timeout=10,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                 "TEST_LOG": str(logfile), "TEST_EXISTING": str(int(existing)),
                 "TEST_BUILD_FAIL": str(int(build_fail)),
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
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse(any(call['args'][0] in ('rm', 'run') for call in calls))

    def test_stopped_container_is_not_reported_as_success(self):
        result, _, _ = self.deployment(running=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("部署与首次配置完成", result.stdout)


if __name__ == "__main__":
    unittest.main()

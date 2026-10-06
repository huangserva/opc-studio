#!/usr/bin/env python3
"""Run checks with synthetic home paths; never start the production server.

Default scope is the portable core. --full additionally requires local source
checkouts for optional contracts, which must be reviewed before importing them.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WECHAT_MODULES = {
    'test_clue_business', 'test_clue_pipeline', 'test_clue_retry',
    'test_clue_workflow', 'test_clues_api', 'test_github_source',
    'test_wechat_radar_pipeline', 'test_wechat_source', 'test_wechat_source_api',
}
EXTERNAL_METHODS = {
    'test_graph_input_names_and_link_types_match_the_deployed_schema': 'ARTICLE_STUDIO_H3_SCHEMA: deployed H3 schema',
    'test_h3_semantic_params_are_accepted_by_the_deployed_api_contract': 'MEDIA_API_SOURCE: external media API builder',
    'test_total_deadline_reaches_existing_child_cleanup_instead_of_orphaning_decrypt': 'ARTICLE_STUDIO_WECHAT_HELPERS: external cleanup helper',
}


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def loopback(address):
    if isinstance(address, str):  # Unix domain socket path.
        return True
    host = address[0]
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Require every external offline contract dependency')
    parser.add_argument('--python-only', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
    node = os.environ.get('ARTICLE_STUDIO_NODE') or shutil.which('node')
    missing = []
    if not node:
        missing.append('Node.js >=22.19.0')
    elif subprocess.run([node, '-e', 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a<22||(a===22&&b<19)?1:0)'], check=False).returncode:
        missing.append('Node.js >=22.19.0')
    if not (ROOT / 'pi-runtime/node_modules/@earendil-works/pi-coding-agent/package.json').is_file():
        missing.append('Pi SDK: npm ci --prefix pi-runtime --ignore-scripts; node pi-runtime/prepare.mjs')
    for binary in ('ffmpeg', 'ffprobe'):
        if not shutil.which(binary):
            missing.append(binary + ' on PATH (synthetic media fixtures)')
    if not args.python_only and not (ROOT / 'node_modules/jsdom/package.json').is_file():
        missing.append('jsdom: npm ci --ignore-scripts')
    original_home = Path.home()
    plugin = original_home / '.openclaw/extensions/memos-local-openclaw-plugin'
    if args.full:
        wechat = Path(os.environ.get('ARTICLE_STUDIO_WECHAT_MODULE', ''))
        if not (wechat / 'wechat_source/__init__.py').is_file():
            missing.append('ARTICLE_STUDIO_WECHAT_MODULE: directory containing the external wechat_source package')
        schema = Path(os.environ.get('ARTICLE_STUDIO_H3_SCHEMA', ''))
        if not schema.is_file():
            missing.append('ARTICLE_STUDIO_H3_SCHEMA: captured deployed H3 schema')
        media = Path(os.environ.get('MEDIA_API_SOURCE', ''))
        if not (media / 'workflows.py').is_file():
            missing.append('MEDIA_API_SOURCE: media API source checkout for its builder contract')
        helpers = Path(os.environ.get('ARTICLE_STUDIO_WECHAT_HELPERS', ''))
        if not (helpers / 'finish_capture.py').is_file():
            missing.append('ARTICLE_STUDIO_WECHAT_HELPERS: external finish_capture helper checkout')
        if not (plugin / 'src/storage/sqlite.ts').is_file():
            missing.append('MemOS Local plugin for installed-plugin synthetic DB contracts')
    if missing:
        print('Offline checks not run; missing prerequisites:', file=sys.stderr)
        for item in missing:
            print('  - ' + item, file=sys.stderr)
        return 2
    print('Scope: full offline contracts' if args.full else 'Scope: portable offline core (external contracts excluded)', flush=True)
    if not args.full:
        print('Excluded external WeChat modules: ' + ', '.join(sorted(WECHAT_MODULES)), flush=True)
        print('Excluded class: test_assistant_memos.MemosInstalledPluginTests (installed MemOS plugin)', flush=True)
    connect, connect_ex, bind = socket.socket.connect, socket.socket.connect_ex, socket.socket.bind
    fixture_ports = set()

    def fixture_bind(instance, address):
        if not loopback(address):
            raise RuntimeError('Offline fixture attempted a non-loopback listener')
        value = bind(instance, address)
        if isinstance(address, tuple):
            fixture_ports.add(instance.getsockname()[1])
        return value

    def offline_connect(instance, address):
        if not loopback(address) or isinstance(address, tuple) and address[1] not in fixture_ports:
            raise ConnectionRefusedError('Offline checks connect only to listeners created by this test process')
        return connect(instance, address)

    def offline_connect_ex(instance, address):
        if not loopback(address) or isinstance(address, tuple) and address[1] not in fixture_ports:
            raise ConnectionRefusedError('Offline checks connect only to listeners created by this test process')
        return connect_ex(instance, address)

    with tempfile.TemporaryDirectory(prefix='content-studio-offline-home-') as home:
        environment = dict(os.environ)
        for name in list(environment):
            if name.startswith(('ARTICLE_STUDIO_', 'MEDIA_API_')):
                del environment[name]
        environment.update(PYTHONDONTWRITEBYTECODE='1', ARTICLE_STUDIO_NODE=node,
                           ARTICLE_STUDIO_REMIO=str(Path(home) / 'missing-remio'),
                           ARTICLE_STUDIO_IMAGE_SKILL=str(Path(home) / 'missing-image-skill'),
                           ARTICLE_STUDIO_MEMOS_DB=str(Path(home) / 'missing-memos.db'),
                           ARTICLE_STUDIO_MEMOS_PLUGIN=str(Path(home) / 'missing-memos-plugin'),
                           ARTICLE_STUDIO_MODEL_CONFIG=str(Path(home) / 'missing-model.json'))
        if args.full:
            for name in ('ARTICLE_STUDIO_WECHAT_MODULE', 'ARTICLE_STUDIO_H3_SCHEMA', 'ARTICLE_STUDIO_WECHAT_HELPERS', 'MEDIA_API_SOURCE'):
                environment[name] = os.environ[name]
            target = Path(home) / '.openclaw/extensions/memos-local-openclaw-plugin'
            target.parent.mkdir(parents=True)
            target.symlink_to(plugin, target_is_directory=True)
        with patch.dict(os.environ, environment, clear=True), patch.object(Path, 'home', return_value=Path(home)), patch.object(socket.socket, 'bind', fixture_bind), patch.object(socket.socket, 'connect', offline_connect), patch.object(socket.socket, 'connect_ex', offline_connect_ex):
            loader = unittest.TestLoader()
            suite = unittest.TestSuite()
            for path in sorted((ROOT / 'tests').glob('test_*.py')):
                if not args.full and path.stem in WECHAT_MODULES:
                    continue
                for test in cases(loader.loadTestsFromName(path.stem)):
                    identity = test.id()
                    method = identity.rsplit('.', 1)[-1]
                    if not args.full and 'MemosInstalledPluginTests.' in identity:
                        continue
                    if not args.full and method in EXTERNAL_METHODS:
                        print('Excluded method: ' + identity + ' (' + EXTERNAL_METHODS[method] + ')', flush=True)
                        continue
                    suite.addTest(test)
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            if result.skipped:
                print('Unexpected skips make this scope incomplete:', file=sys.stderr)
                for identity, reason in result.skipped:
                    print('  ' + identity.id() + ': ' + reason, file=sys.stderr)
                return 1
            if not result.wasSuccessful():
                return 1
            if not args.python_only:
                javascript = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'tests').glob('test_*.cjs'))
                javascript += ['tests/test_request_context.mjs']
                if subprocess.run([node, '--test', *javascript], check=False).returncode:
                    return 1
    print('PASS: requested offline scope; online acceptance is separate.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

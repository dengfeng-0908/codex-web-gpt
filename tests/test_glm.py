import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from web_gpt import glm


class GlmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'WEB_GPT_DATA_DIR': str(self.root / 'data'),
            'GLM_CODING_API_KEY': 'fixture-secret',
            'ANTHROPIC_BASE_URL': 'https://wrong.invalid',
            'CLAUDE_CODE_OAUTH_TOKEN': 'unrelated-auth',
        })
        self.env.start()
        glm.configure('bigmodel', 'glm-5.3')
        self.executable = self.root / 'claude'

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, code):
        self.executable.write_text(f'#!{sys.executable}\n' + code)
        self.executable.chmod(0o700)
        return patch.object(glm.shutil, 'which', return_value=str(self.executable))

    def test_delegation_uses_stdin_and_isolates_tools_settings_and_credentials(self):
        with self.cli('''import json,os,sys
args=sys.argv[1:]
assert args[args.index('--tools')+1] == ''
assert args[args.index('--setting-sources')+1] == ''
assert args[args.index('--model')+1] == 'glm-5.3'
assert args[args.index('--effort')+1] == 'max'
assert '--strict-mcp-config' in args and '--no-session-persistence' in args
assert os.environ['ANTHROPIC_BASE_URL'] == 'https://open.bigmodel.cn/api/anthropic'
assert os.environ['ANTHROPIC_AUTH_TOKEN'] == 'fixture-secret'
assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
assert 'fixture-secret' not in ' '.join(args)
print(json.dumps({'result':'Reviewed: '+sys.stdin.read(),'duration_ms':10}))
'''):
            result = glm.ask('def f(): return 1', timeout=5)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], 'Reviewed: def f(): return 1')
        self.assertEqual(result['requested_effort'], 'max')

    def test_provider_error_redacts_key_and_never_returns_success(self):
        with self.cli('''import json,os
print(json.dumps({'is_error':True,'result':'rejected '+os.environ['ANTHROPIC_AUTH_TOKEN']}))
'''):
            result = glm.ask('review code', timeout=5)
        self.assertEqual(result['status'], 'provider_error')
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_zcode_source_is_read_without_copying_key_and_picks_explicit_provider(self):
        source = self.root / 'zcode.json'
        source.write_text(json.dumps({'provider':{'chosen':{'options':{'apiKey':'local-source-key'}}}}))
        glm.configure('zai', 'glm-5.2', str(source), 'chosen')
        self.assertEqual(glm.credential(glm.load_config()), 'local-source-key')
        self.assertNotIn('local-source-key', glm.config_path().read_text())
        self.assertEqual(glm.config_path().stat().st_mode & 0o777, 0o600)

    def test_missing_key_and_invalid_model_do_not_launch_cli(self):
        with patch.dict(os.environ, {'GLM_CODING_API_KEY':''}), patch.object(glm.subprocess, 'run') as run:
            self.assertEqual(glm.ask('review code')['status'], 'configuration_error')
            run.assert_not_called()
        with patch.object(glm.subprocess, 'run') as run:
            self.assertEqual(glm.ask('review code', model='sonnet')['status'], 'invalid_input')
            run.assert_not_called()

    def test_timeout_returns_uncertain_result_without_retry(self):
        with self.cli('import time\ntime.sleep(5)\n'):
            result = glm.ask('review code', timeout=1)
        self.assertEqual(result['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()

"""Smoke tests for evidence.py and dossier.py (no Chrome/Swift/network)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import dossier  # noqa: E402
import evidence  # noqa: E402


ROWS = [
    {
        'seq': 1, 'name': '示例达人', 'nick': '示例昵称', 'dur': 30,
        'play': 60000, 'shot': 60000, 'interact': 0.005833,
        'finish_use': 7.0, 'inc': 300, 'fee': 500, 'subtotal': 530,
        'pub_link': 'https://www.douyin.com/video/1234567890123456789',
        's1': '', 's2': '', 's3': '',
        'a1': '通过', 'a2': '通过', 'a3': '通过', 'a4': '通过',
    }
]


class TestEvidenceDossier(unittest.TestCase):
    def test_build(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'work_rows.json'), 'w', encoding='utf-8') as f:
                json.dump(ROWS, f, ensure_ascii=False)
            with open(os.path.join(d, 'audit_state.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'source': 'source.xlsx', 'rule_ver': 'test', 'calib': 'col100',
                    'audited_at': '2026-10-20T00:00:00',
                    'precheck_issues': [], 'manual': {},
                }, f, ensure_ascii=False)

            ev = os.path.join(d, '截图证据清单.xlsx')
            path, n_shots, n_rows = evidence.build(d, ev, os.path.join(d, 'missing.json'))
            self.assertTrue(os.path.exists(path))
            self.assertEqual(n_shots, 0)
            self.assertEqual(n_rows, 1)

            args = types.SimpleNamespace(
                work_dir=d, source=None, rules=None, out_dir=d,
                date='20261020', title='测试底档',
            )
            md, htm = dossier.build(args)
            self.assertTrue(os.path.exists(md))
            self.assertTrue(os.path.exists(htm))
            text = Path(md).read_text(encoding='utf-8')
            for section in ('一、任务标识', '七、结果摘要', '十、产物清单'):
                self.assertIn(section, text)
            self.assertIn('示例达人', json.dumps(ROWS, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()

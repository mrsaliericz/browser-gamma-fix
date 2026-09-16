import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import app_updates as u
import tray_gamma22 as tray
import hot_attach_gamma22 as hot


def metadata(tag='v0.8.0'):
    return dict(tag_name=tag,draft=False,prerelease=False,assets=[dict(
        name='BrowserGammaFix-win64.zip',size=123,digest='sha256:'+'a'*64,
        browser_download_url=f'https://github.com/{u.REPO}/releases/download/{tag}/BrowserGammaFix-win64.zip')])


class AppUpdateTests(unittest.TestCase):
    def test_release_order_and_stable_only(self):
        self.assertIsNone(u.parse_release(metadata('v0.6.0'),'0.7.0-beta.1'))
        self.assertIsNotNone(u.parse_release(metadata('v0.7.0'),'0.7.0-beta.1'))
        self.assertIsNone(u.parse_release(metadata('v0.7.0'),'0.7.0'))
        data=metadata();data['prerelease']=True
        self.assertIsNone(u.parse_release(data,'0.7.0'))

    def test_missing_digest_wrong_origin_and_oversize_rejected(self):
        for field,value in [('digest',None),('browser_download_url','https://evil.example/test.zip'),('size',u.MAX_ARCHIVE+1)]:
            with self.subTest(field=field):
                data=metadata();data['assets'][0][field]=value
                with self.assertRaises(ValueError):u.parse_release(data,'0.7.0')

    def test_persistent_cooldown_new_generation_and_manual_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[1000000];p=Path(tmp)/'checks.json'
            s=u.CheckSchedule(p,lambda:now[0])
            self.assertTrue(s.claim('unsupported:a'))
            self.assertFalse(s.claim('unsupported:a'))
            self.assertFalse(s.claim('unsupported:b'))
            now[0]+=u.COOLDOWN+1
            s=u.CheckSchedule(p,lambda:now[0])
            self.assertFalse(s.claim('unsupported:a'))
            self.assertTrue(s.claim('unsupported:b'))
            self.assertTrue(s.claim(manual=True))
            now[0]+=u.INTERVAL+1
            self.assertTrue(s.claim('unsupported:a'))

    def test_archive_paths_and_case_collisions_rejected(self):
        for path in ['../outside','Gamma22Tray/../../outside','Gamma22Tray/C:/bad',
                     'Gamma22Tray/CON.txt','Gamma22Tray\\bad','/Gamma22Tray/bad']:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                archive=Path(tmp)/'a.zip'
                with zipfile.ZipFile(archive,'w') as z:z.writestr(path,b'x')
                with self.assertRaises(ValueError):u.extract_package(archive,Path(tmp)/'out')
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'a.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('Gamma22Tray/A',b'x');z.writestr('Gamma22Tray/a',b'x')
            with self.assertRaises(ValueError):u.extract_package(archive,Path(tmp)/'out')

    def test_valid_package_and_checksum_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'a.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('Gamma22Tray/Gamma22Tray.exe',b'test exe')
                z.writestr('Gamma22Tray/_internal/test.dll',b'test dll')
            installed=u.extract_package(archive,root/'install')
            self.assertTrue((installed/'_internal/test.dll').is_file())
            blob=archive.read_bytes()
            release=u.Release('v0.8.0','unused','0'*64,len(blob))
            with mock.patch.object(u,'open_url',return_value=io.BytesIO(blob)):
                with self.assertRaisesRegex(ValueError,'SHA-256'):
                    u.prepare(release,installed/'Gamma22Tray.exe')
            self.assertEqual((installed/'Gamma22Tray.exe').read_bytes(),b'test exe')

    def test_successful_swap_and_launch_failure_rollback(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'app';staged=Path(tmp)/'staged';backup=Path(tmp)/'backup'
                target.mkdir();staged.mkdir()
                (target/'Gamma22Tray.exe').write_bytes(b'old')
                (staged/'Gamma22Tray.exe').write_bytes(b'new')
                def launch(exe):
                    self.assertEqual(exe.read_bytes(),b'new')
                    if fail:raise OSError('Launch blocked')
                if fail:
                    with self.assertRaises(OSError):u.swap_package(target,staged,backup,launch)
                    self.assertEqual((target/'Gamma22Tray.exe').read_bytes(),b'old')
                    self.assertEqual((Path(tmp)/'backup-failed/Gamma22Tray.exe').read_bytes(),b'new')
                else:
                    u.swap_package(target,staged,backup,launch)
                    self.assertEqual((target/'Gamma22Tray.exe').read_bytes(),b'new')
                    self.assertEqual((backup/'Gamma22Tray.exe').read_bytes(),b'old')

    def test_unrelated_installation_files_block_auto_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Gamma22Tray.exe').touch();(root/'_internal').mkdir()
            self.assertEqual(u.validate_installation(root),root.resolve())
            (root/'my-document.txt').write_text('preserve me')
            with self.assertRaises(ValueError):u.validate_installation(root)
            self.assertEqual((root/'my-document.txt').read_text(),'preserve me')

    def test_only_planner_layout_failure_triggers_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser=Path(tmp)/'chrome.exe';dll=Path(tmp)/'chrome.dll'
            browser.touch();dll.write_bytes(b'fake')
            for error,expected in [(hot.PatchError('layout'),1),(PermissionError('access'),0)]:
                callback=mock.Mock()
                g=tray.BrowserGenerations(browser,planner=mock.Mock(side_effect=error),on_unsupported=callback)
                for _ in range(2):
                    with self.assertRaises(type(error)):g.register(dll)
                self.assertEqual(callback.call_count,expected)


if __name__=='__main__':unittest.main()

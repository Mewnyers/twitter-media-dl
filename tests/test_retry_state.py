import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from twitter_media_dl import cli, downloader, maintenance, storage


def media(number, **changes):
    return {"tweet_id": str(number), "media_index": 1, "filename": f"{number}.jpg",
            "url": f"https://example.test/{number}", "created_at_sort": f"202610020000{number:02}",
            "legacy_filenames": [], **changes}


class RetryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'user'
        self.folder.mkdir()
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(patch.object(downloader.time, 'sleep'))

    def state(self):
        return storage.load_user_state('user', str(self.root))

    def test_failure_advances_boundary_and_empty_fetch_retries_only_pending(self):
        with patch.object(downloader, 'download_file', side_effect=['HTTP 403', None]) as download:
            result = downloader.download_user_media('user', [media(1), media(2)], self.folder)
        self.assertEqual(result.failed, 1)
        self.assertEqual(self.state()['watermark'], media(2)['created_at_sort'])
        self.assertEqual(list(self.state()['pending_downloads']), ['1:1'])
        with patch.object(downloader, 'download_file', return_value='HTTP 403') as download:
            downloader.download_user_media('user', [], self.folder)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.state()['pending_downloads']['1:1']['attempts'], 2)
        self.assertEqual(self.state()['watermark'], media(2)['created_at_sort'])

    def test_fresh_url_deduplicates_pending_and_success_removes_it(self):
        with patch.object(downloader, 'download_file', return_value='HTTP 403'):
            downloader.download_user_media('user', [media(1)], self.folder)
        with patch.object(downloader, 'download_file', return_value=None) as download:
            downloader.download_user_media('user', [media(1, url='https://example.test/new')], self.folder)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], 'https://example.test/new')
        self.assertEqual(self.state()['pending_downloads'], {})

    def test_interrupt_keeps_failure_checkpoint(self):
        with patch.object(downloader, 'download_file', side_effect=['HTTP 403', KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                downloader.download_user_media('user', [media(1), media(2)], self.folder)
        self.assertEqual(self.state()['watermark'], media(1)['created_at_sort'])
        self.assertIn('1:1', self.state()['pending_downloads'])

    def test_atomic_save_failure_preserves_old_state_and_stops_user(self):
        storage.save_active_state('user', '20261001000000', downloads_dir=str(self.root))
        before = self.state()
        with patch.object(storage.os, 'replace', side_effect=OSError('disk error')):
            with patch.object(downloader, 'download_file', return_value='HTTP 403') as download:
                with self.assertRaises(RuntimeError):
                    downloader.download_user_media('user', [media(1), media(2)], self.folder)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(list((self.root / '.state').iterdir())), 1)

    def test_scan_records_missing_and_update_retries_without_new_items(self):
        (self.folder / '2.jpg').write_bytes(b'ok')
        def checkpoint(watermark, pending):
            storage.save_active_state('user', watermark, pending_downloads=pending, downloads_dir=str(self.root))
        result = maintenance.scan_downloaded_items([media(1), media(2)], [self.folder], checkpoint=checkpoint)
        self.assertEqual(result.missing, 1)
        self.assertEqual(self.state()['watermark'], media(2)['created_at_sort'])
        self.assertEqual(self.state()['pending_downloads']['1:1']['attempts'], 0)
        with patch.object(downloader, 'download_file', return_value=None) as download:
            downloader.download_user_media('user', [], self.folder)
        self.assertEqual(download.call_count, 1)
        self.assertFalse(self.state()['pending_downloads'])

    def test_existing_file_resolves_pending_without_download(self):
        with patch.object(downloader, 'download_file', return_value='HTTP 403'):
            downloader.download_user_media('user', [media(1)], self.folder)
        (self.folder / '1.jpg').write_bytes(b'ok')
        with patch.object(downloader, 'download_file') as download:
            downloader.download_user_media('user', [], self.folder)
        download.assert_not_called()
        self.assertFalse(self.state()['pending_downloads'])

    def test_media_rate_limit_stops_before_next_item(self):
        with patch.object(downloader, 'download_file', return_value='HTTP 429') as download:
            result = downloader.download_user_media('user', [media(1), media(2)], self.folder)
        self.assertTrue(result.rate_limited)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.state()['watermark'], media(1)['created_at_sort'])

    def test_version_one_is_read_without_losing_metadata(self):
        storage.write_user_state('user', {'version': 1, 'watermark': '20261001000000', 'folders': ['old']}, str(self.root))
        downloader.download_user_media('user', [], self.folder)
        self.assertEqual(self.state()['version'], 2)
        self.assertEqual(self.state()['folders'], ['old'])

    def test_invalid_state_is_not_overwritten(self):
        storage.write_user_state('user', {'pending_downloads': []}, str(self.root))
        with self.assertRaises(RuntimeError):
            downloader.download_user_media('user', [], self.folder)

    def test_list_reports_failed_media_and_unprocessed_users(self):
        item = {**media(1), 'last_error': 'HTTP 429', 'attempts': 1}
        result = downloader.UserResult('first', {'1:1': item}, rate_limited=True)
        with patch.object(cli, 'download_user', return_value=result) as download:
            self.assertFalse(cli.download_user_list(['first', 'second'], '', ''))
        self.assertEqual(download.call_count, 1)
        self.assertIn('@first', self.output.getvalue())
        self.assertIn('1.jpg', self.output.getvalue())
        self.assertIn('HTTP 429', self.output.getvalue())
        self.assertIn('1 ユーザー未処理', self.output.getvalue())

    def test_bulk_empty_api_retries_pending_and_keeps_diff_delay(self):
        with patch.object(downloader, 'download_file', return_value='HTTP 403'):
            downloader.download_user_media('user', [media(1)], self.folder)
        users = {'user': [self.folder], 'other': [self.folder]}
        with patch.object(maintenance, 'DOWNLOADS_DIR', str(self.root)), \
             patch.object(maintenance, 'find_downloaded_user_folders', return_value=users), \
             patch.object(maintenance, 'prepare_output_dir', return_value=self.folder), \
             patch.object(maintenance, 'fetch_tweets', return_value=([], SimpleNamespace(name='user'))), \
             patch.object(maintenance, 'wait_between_update_all_users') as diff, \
             patch.object(maintenance, 'wait_between_users') as full, \
             patch.object(downloader, 'download_file', return_value='HTTP 403') as download:
            self.assertFalse(maintenance.update_all_downloads('', ''))
        diff.assert_called_once()
        full.assert_not_called()
        self.assertEqual(download.call_count, 1)
        self.assertIn('@user', self.output.getvalue())

    def test_profile_parse_failure_is_not_permanently_unavailable(self):
        self.assertFalse(maintenance._is_unavailable_user_error(RuntimeError('ユーザー取得失敗: parser error')))

    def test_same_tweet_multiple_media_keep_separate_pending(self):
        second = media(1, media_index=2, filename='second.jpg')
        with patch.object(downloader, 'download_file', return_value='HTTP 403'):
            downloader.download_user_media('user', [media(1), second], self.folder)
        self.assertEqual(set(self.state()['pending_downloads']), {'1:1', '1:2'})

    def test_rename_failure_is_retryable(self):
        with patch.object(downloader, 'rename_legacy_file', side_effect=OSError('rename denied')):
            downloader.download_user_media('user', [media(1)], self.folder)
        self.assertIn('rename denied', self.state()['pending_downloads']['1:1']['last_error'])

    def test_failed_file_write_does_not_leave_final_file(self):
        response = io.BytesIO(b'image')
        with patch.object(downloader.urllib.request, 'urlopen', return_value=response), \
             patch.object(downloader, 'create_ssl_context'), \
             patch.object(Path, 'replace', side_effect=OSError('disk error')):
            error = downloader.download_file('https://example.test/image', self.folder / '1.jpg')
        self.assertIn('disk error', error)
        self.assertFalse((self.folder / '1.jpg').exists())
        self.assertFalse((self.folder / '1.jpg.part').exists())

    def test_single_user_empty_fetch_still_processes_pending(self):
        with patch.object(downloader, 'download_file', return_value='HTTP 403'):
            downloader.download_user_media('user', [media(1)], self.folder)
        with patch.object(cli, 'load_since_datetime', return_value=None), \
             patch.object(cli, 'fetch_tweets', return_value=([], SimpleNamespace(name='user'))), \
             patch.object(cli, 'prepare_output_dir', return_value=self.folder), \
             patch.object(downloader, 'download_file', return_value='HTTP 403') as download:
            result = cli.download_user('user', max_count=None, include_retweets=False, full=False,
                                       anonymize=False, debug=False, auth_token='', ct0='')
        self.assertFalse(result.success)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(result.pending['1:1']['attempts'], 2)

    def test_bulk_api_failure_report_preserves_boundary_and_continues(self):
        storage.save_active_state('user', '20261001000000', downloads_dir=str(self.root))
        users = {'user': [self.folder], 'other': [self.folder]}
        with patch.object(maintenance, 'DOWNLOADS_DIR', str(self.root)), \
             patch.object(maintenance, 'find_downloaded_user_folders', return_value=users), \
             patch.object(maintenance, 'fetch_tweets', side_effect=maintenance.TwitterFetchError('parser error')) as fetch, \
             patch.object(maintenance, 'wait_between_update_all_users'):
            self.assertFalse(maintenance.update_all_downloads('', ''))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.state()['watermark'], '20261001000000')
        self.assertEqual(self.state()['status'], 'active')
        self.assertIn('@other', self.output.getvalue())

    def test_bulk_full_fetch_keeps_long_wait(self):
        users = {'user': [self.folder], 'other': [self.folder]}
        with patch.object(maintenance, 'DOWNLOADS_DIR', str(self.root)), \
             patch.object(maintenance, 'find_downloaded_user_folders', return_value=users), \
             patch.object(maintenance, 'prepare_output_dir', return_value=self.folder), \
             patch.object(maintenance, 'fetch_tweets', return_value=([], SimpleNamespace(name='user'))), \
             patch.object(maintenance, 'wait_between_update_all_users') as diff, \
             patch.object(maintenance, 'wait_between_users') as full:
            self.assertTrue(maintenance.update_all_downloads('', '', full=True))
        full.assert_called_once()
        diff.assert_not_called()

    def test_bulk_rate_limit_reports_unprocessed_users(self):
        users = {'user': [self.folder], 'other': [self.folder]}
        with patch.object(maintenance, 'DOWNLOADS_DIR', str(self.root)), \
             patch.object(maintenance, 'find_downloaded_user_folders', return_value=users), \
             patch.object(maintenance, 'fetch_tweets', side_effect=maintenance.TwitterFetchError('HTTP 429')) as fetch:
            self.assertFalse(maintenance.update_all_downloads('', ''))
        self.assertEqual(fetch.call_count, 1)
        self.assertIn('1 ユーザー未処理', self.output.getvalue())

    def test_save_failure_carries_unsaved_media_for_report(self):
        with patch.object(storage.os, 'replace', side_effect=OSError('disk error')), \
             patch.object(downloader, 'download_file', return_value='HTTP 403'):
            with self.assertRaises(storage.StateSaveError) as caught:
                downloader.download_user_media('user', [media(1)], self.folder)
        self.assertIn('1:1', caught.exception.pending_downloads)


if __name__ == '__main__':
    unittest.main()

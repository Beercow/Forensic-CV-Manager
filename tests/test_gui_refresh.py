"""Exercise real Tk widgets against synthetic SQLite data, without starting App."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import App, RecordsTab
from attachments import add_files, delete_attachment, get_attachment, thumbnail_photo
from database import Database


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.db = Database(self.path / 'test.sqlite3')
        self.root = tk.Tk()
        self.root.withdraw()
        self.ids = [self.db.insert_row('training', {
            'course_name': name, 'hours': hours, 'attended_date': day,
        }) for name, hours, day in [
            ('Course 10', 10, '2026-01-01'), ('Course 2', 2, '2025-01-01'), ('', None, ''),
        ]]
        self.changed = Mock()
        self.tab = RecordsTab(self.root, self.db, 'training', lambda _: None, self.changed)

    def tearDown(self):
        self.root.destroy()
        self.db.close()
        self.temp.cleanup()

    def values(self, column):
        return [self.tab.tree.set(iid, column) for iid in self.tab.tree.get_children()]

    def test_sort_reuses_rows_without_sql_autosizing_or_images(self):
        self.tab.tree.selection_set(str(self.ids[0]))
        self.tab.tree.focus(str(self.ids[0]))
        queries = []
        self.db.conn.set_trace_callback(queries.append)
        with patch.object(self.tab.tree, 'insert', wraps=self.tab.tree.insert) as insert, \
             patch.object(self.tab.tree, 'delete', wraps=self.tab.tree.delete) as delete, \
             patch.object(self.tab, '_autosize_all_columns') as sizing:
            self.tab.sort_by('course_name')
            self.assertEqual(self.values('course_name'), ['Course 2', 'Course 10', ''])
            self.tab.sort_by('course_name')
            self.assertEqual(self.values('course_name'), ['Course 10', 'Course 2', ''])
            self.tab.sort_by('hours')
            self.assertEqual(self.values('hours'), ['2.0', '10.0', ''])
            self.tab.sort_by('hours')
            self.assertEqual(self.values('hours'), ['10.0', '2.0', ''])
            self.tab.sort_by('attended_date')
            self.assertEqual(self.values('attended_date'), ['2025-01-01', '2026-01-01', ''])
            insert.assert_not_called()
            delete.assert_not_called()
            sizing.assert_not_called()
        self.assertEqual(queries, [])
        self.assertEqual(self.tab.tree.selection(), (str(self.ids[0]),))
        self.assertEqual(self.tab.tree.focus(), str(self.ids[0]))

    def test_refresh_inserts_only_new_row_and_updates_existing(self):
        with patch.object(self.tab.tree, 'insert', wraps=self.tab.tree.insert) as insert, \
             patch.object(self.tab.tree, 'delete', wraps=self.tab.tree.delete) as delete:
            self.tab.refresh()
            insert.assert_not_called()
            delete.assert_not_called()
            new = self.db.insert_row('training', {'course_name': 'New'})
            self.tab._after_mutation()
            self.assertEqual(insert.call_count, 1)
            self.changed.assert_called_once()
            self.db.update_row('training', new, {'course_name': 'Edited'})
            self.tab.refresh()
            self.assertEqual(self.tab.tree.set(str(new), 'course_name'), 'Edited')
            self.db.delete_row('training', new)
            self.tab.refresh()
            self.assertFalse(self.tab.tree.exists(str(new)))

    def test_thumbnail_cache_search_delete_and_profile_isolation(self):
        source = self.path / 'synthetic.txt'
        source.write_text('Synthetic document')
        attachment = add_files(self.db, 'training', self.ids[0], [source])[0]
        with patch('app.thumbnail_photo', wraps=thumbnail_photo) as thumbnail, \
             patch('app.get_attachment', wraps=get_attachment) as blob:
            self.tab.refresh()
            image = self.tab._thumbnail_refs[str(self.ids[0])]
            self.assertEqual((image.width(), image.height()), (27, 27))
            self.tab.refresh()
            self.tab.sort_by('course_name')
            self.tab.search_var.set('no match')
            self.tab.search_var.set('')
            self.assertEqual(thumbnail.call_count, 1)
            self.assertEqual(blob.call_count, 1)
            delete_attachment(self.db, attachment)
            self.tab.refresh()
            self.assertEqual(self.tab._thumbnail_cache, {})
            self.assertEqual(self.tab.tree.item(str(self.ids[0]), 'image'), '')
            self.assertNotIn(str(self.ids[0]), self.tab._thumbnail_refs)
        pid = self.db.create_profile('Synthetic second profile')
        self.db.set_current_profile(pid)
        self.tab.refresh()
        self.assertEqual(self.tab.tree.get_children(), ())
        self.assertEqual(self.tab._thumbnail_cache, {})

    def test_thumbnail_hash_change_and_multiple_attachments(self):
        source = self.path / 'a.txt'
        source.write_text('A')
        first = add_files(self.db, 'training', self.ids[0], [source])[0]
        other = self.path / 'b.txt'
        other.write_text('B')
        add_files(self.db, 'training', self.ids[0], [other])
        with patch('app.thumbnail_photo', wraps=thumbnail_photo) as thumbnail:
            self.tab.refresh()
            self.assertEqual(self.tab.tree.item(str(self.ids[0]), 'text'), '2')
            self.db.conn.execute('UPDATE attachments SET sha256=?, content=? WHERE id=?', ('changed', b'changed', first))
            self.db.conn.commit()
            self.tab.refresh()
            self.assertEqual(thumbnail.call_count, 2)
            self.assertEqual(len(self.tab._thumbnail_cache), 1)

    def test_separator_double_click_autosizes_column(self):
        self.tab.tree.column('course_name', width=70)
        with patch.object(self.tab.tree, 'identify_region', return_value='separator'), \
             patch.object(self.tab.tree, 'identify_column', return_value='#2'):
            self.assertEqual(self.tab._double_click(SimpleNamespace(x=5, y=5)), 'break')
        self.assertGreater(self.tab.tree.column('course_name', 'width'), 70)

    def test_column_widths_survive_sort_and_grow_only_for_changed_content(self):
        self.tab.tree.column('course_name', width=400)
        self.tab.sort_by('course_name')
        self.tab.refresh()
        self.assertEqual(self.tab.tree.column('course_name', 'width'), 400)
        self.db.update_row('training', self.ids[0], {'course_name': 'Long synthetic title ' * 10})
        self.tab.refresh()
        self.assertGreater(self.tab.tree.column('course_name', 'width'), 400)

    def test_present_dates_and_core_training_sort_rules(self):
        employment = RecordsTab(self.root, self.db, 'employment', lambda _: None)
        for end in ('2020', 'Present', ''):
            self.db.insert_row('employment', {'employer': 'Synthetic', 'title': 'Test', 'end_date': end})
        employment.refresh()
        employment.sort_by('end_date')
        self.assertEqual([employment.tree.set(i, 'end_date') for i in employment.tree.get_children()], ['2020', 'Present', ''])
        employment.sort_by('end_date')
        self.assertEqual([employment.tree.set(i, 'end_date') for i in employment.tree.get_children()], ['Present', '2020', ''])
        self.db.update_row('training', self.ids[0], {'core_training': 1})
        self.tab.refresh()
        self.tab.sort_by('core_training')
        # Preserve the existing blank-last treatment of false/zero values.
        self.assertEqual(self.values('core_training'), ['Yes', 'No', 'No'])

    def test_large_synthetic_table_refresh_uses_two_selects_no_blob_reads(self):
        for i in range(500):
            self.db.insert_row('training', {'course_name': f'Synthetic {i}', 'hours': i})
        queries = []
        self.db.conn.set_trace_callback(queries.append)
        self.tab.refresh()
        self.assertEqual(len(self.tab.tree.get_children()), 503)
        selects = [query for query in queries if query.lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(selects), 2)
        self.assertFalse(any('CREATE' in query.upper() for query in queries))
        self.assertFalse(any('SELECT * FROM attachments' in query for query in queries))

    def test_add_edit_delete_callbacks_and_attachment_failure(self):
        dialog = SimpleNamespace(result={'course_name': 'Dialog record'}, attachment_picker=None)
        with patch('app.RecordDialog', return_value=dialog), patch.object(self.tab, 'wait_window'), \
             patch('app.messagebox.askyesno', return_value=True):
            self.tab.add()
            row = next(r for r in self.db.list_rows('training') if r['course_name'] == 'Dialog record')
            self.tab.tree.selection_set(str(row['id']))
            dialog.result = {'course_name': 'Edited dialog'}
            self.tab.edit()
            self.tab.delete()
            self.assertEqual(self.changed.call_count, 3)
            dialog.attachment_picker = Mock()
            dialog.attachment_picker.commit.side_effect = RuntimeError('Synthetic failure')
            with self.assertRaises(RuntimeError):
                self.tab.add()
            self.assertEqual(self.changed.call_count, 4)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'dashboard.sqlite3')
        # Bypass App startup (portable DB, splash and update checker).
        self.app = App.__new__(App)
        tk.Tk.__init__(self.app)
        self.app.withdraw()
        self.app.db = self.db
        self.app.db_path = Path(self.temp.name)
        self.app.theme_name = 'light'
        self.app.notebook = __import__('tkinter.ttk', fromlist=['Notebook']).Notebook(self.app)
        self.app.set_status = lambda _: None
        self.app._dashboard_tab()

    def tearDown(self):
        self.app.destroy()
        self.db.close()
        self.temp.cleanup()

    def test_mutations_coalesce_and_refresh_metrics_without_widget_rebuild(self):
        labels = tuple(self.app._metric_labels)
        self.db.insert_row('training', {'course_name': 'Synthetic', 'hours': 8})
        with patch.object(self.app, 'refresh_dashboard', wraps=self.app.refresh_dashboard) as refresh:
            self.app.invalidate_dashboard()
            self.app.invalidate_dashboard()
            self.app.update_idletasks()
            self.assertEqual(refresh.call_count, 1)
        self.assertEqual(tuple(self.app._metric_labels), labels)
        self.assertEqual(labels[0].cget('text'), '8.00')
        self.assertEqual(labels[1].cget('text'), '1')
        self.assertFalse(self.app._dashboard_dirty)
        self.assertIsNone(self.app._dashboard_pending)

    def test_dashboard_selection_refreshes_dirty_profile_and_date(self):
        pid = self.db.create_profile('Second synthetic profile')
        self.db.set_current_profile(pid)
        self.app._on_tab_changed()
        self.assertEqual(self.app._dashboard_key[0], pid)
        self.app._dashboard_key = None
        with patch.object(self.app, 'refresh_dashboard', wraps=self.app.refresh_dashboard) as refresh:
            self.app._on_tab_changed()
            self.app._on_tab_changed()
            self.assertEqual(refresh.call_count, 1)

    def test_dashboard_follows_actual_record_dialog_mutations(self):
        tab = RecordsTab(self.app.notebook, self.db, 'training', lambda _: None, self.app.invalidate_dashboard)
        dialog = SimpleNamespace(result={'course_name': 'Synthetic', 'hours': 8}, attachment_picker=None)
        with patch('app.RecordDialog', return_value=dialog), patch.object(tab, 'wait_window'), \
             patch('app.messagebox.askyesno', return_value=True):
            tab.add()
            self.app.update_idletasks()
            self.assertEqual(self.app._metric_labels[0].cget('text'), '8.00')
            tab.tree.selection_set(tab.tree.get_children()[0])
            dialog.result = {'hours': 12}
            tab.edit()
            self.app.update_idletasks()
            self.assertEqual(self.app._metric_labels[0].cget('text'), '12.00')
            tab.delete()
            self.app.update_idletasks()
            self.assertEqual(self.app._metric_labels[0].cget('text'), '0.00')
            self.assertEqual(self.app._metric_labels[1].cget('text'), '0')


if __name__ == '__main__':
    unittest.main()

"""Measure real Tk refreshes; optional databases are copied, never edited in place.

Run from the repository root: python tools/benchmark_refresh.py
Use --database PATH to measure private data. Output contains aggregates only.
"""
import argparse
import cProfile
import json
import sqlite3
import statistics
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import RecordsTab
from attachments import add_files
from database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--records', type=int, default=1000)
    args = parser.parse_args()
    if args.samples < 1 or args.records < 1:
        parser.error('samples and records must be positive')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        target = path / 'benchmark.sqlite3'
        if args.database:
            # A read-only backup also includes committed WAL data.
            source = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        db = Database(target)
        root = tk.Tk()
        root.withdraw()
        try:
            if not args.database:
                document = path / 'synthetic.txt'
                document.write_text('Synthetic test attachment')
                for index in range(args.records):
                    record = db.insert_row('training', {
                        'course_name': f'Synthetic course {index}', 'hours': index % 8,
                        'attended_date': '2026-01-01',
                    })
                    if index < 100:
                        add_files(db, 'training', record, [document])
            profile = cProfile.Profile()
            results = {'records': db.count('training'), 'samples': args.samples}
            with profile:
                start = time.perf_counter()
                tab = RecordsTab(root, db, 'training', lambda _: None)
                results['initial_load_ms'] = round((time.perf_counter() - start) * 1000, 3)

            def measure(name, action):
                samples = []
                for _ in range(args.samples):
                    with profile:
                        start = time.perf_counter()
                        action()
                        samples.append((time.perf_counter() - start) * 1000)
                results[name + '_median_ms'] = round(statistics.median(samples), 3)

            measure('unchanged_refresh', tab.refresh)
            measure('sort', lambda: tab.sort_by('course_name'))

            def mutate():
                record = db.insert_row('training', {'course_name': 'Synthetic added row', 'hours': 1})
                tab.refresh()
                db.delete_row('training', record)
                tab.refresh()

            measure('add_delete_refresh_cycle', mutate)
            profile.create_stats()
            results['profile_calls'] = {
                name: sum(value[0] for key, value in profile.stats.items() if key[2] == name)
                for name in ('thumbnail_photo', 'preview_image', '_autosize_column', 'list_attachments', 'get_attachment')
            }
            print(json.dumps(results, indent=2))
        finally:
            root.destroy()
            db.close()


if __name__ == '__main__':
    main()

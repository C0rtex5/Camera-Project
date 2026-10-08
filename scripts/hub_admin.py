"""Provision local accounts and consistent database/evidence backups."""
import argparse
import getpass
import json
import os
import shutil
from pathlib import Path
from src.hub.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=os.getenv('SENTINEL_DATA_DIR', 'data/hub'))
    commands = parser.add_subparsers(dest='command', required=True)
    user = commands.add_parser('user')
    user.add_argument('username')
    user.add_argument('--role', choices=['administrator', 'operator', 'viewer'], default='administrator')
    backup = commands.add_parser('backup')
    backup.add_argument('destination')
    restore = commands.add_parser('restore')
    restore.add_argument('source')
    args = parser.parse_args()
    if args.command == 'restore':
        # Restore only into an empty data directory while the service is stopped.
        root, source = Path(args.data_dir), Path(args.source)
        if not (source / 'hub.sqlite3').is_file() or not (source / 'evidence').is_dir():
            parser.error('Backup must contain hub.sqlite3 and evidence/')
        if root.exists() and any(root.iterdir()):
            parser.error('Restore requires an empty destination and a stopped hub')
        shutil.copytree(source, root, dirs_exist_ok=True)
        return
    store = Store(args.data_dir)
    if args.command == 'user':
        password = getpass.getpass('Password (at least 12 characters): ')
        if password != getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match')
        store.add_user(args.username, password, args.role)
        print('Local account saved; existing sessions revoked.')
    elif args.command == 'backup':
        destination = Path(args.destination)
        if destination.exists():
            parser.error('Choose a new backup destination')
        destination.mkdir(parents=True, mode=0o700)
        # Stop the hub first to ensure database references and evidence match.
        store.backup(destination / 'hub.sqlite3')
        shutil.copytree(store.root / 'evidence', destination / 'evidence')
        (destination / 'backup.json').write_text(json.dumps({'format': 1, 'includes': ['database', 'evidence']}))
        print('Backup saved. Camera secret files must be backed up separately.')


if __name__ == '__main__':
    main()

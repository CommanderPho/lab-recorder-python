"""
CFG Loader for Lab Recorder Python

This module adds the ability to load LabRecorder .cfg files (from the C++ app)
and run the Python Lab Recorder using those settings, without modifying
existing files.

Usage:
  python -m labrecorder.cfg_loader -c path/to/LabRecorder.cfg

The loader supports a practical subset of the C++ .cfg format:
- StudyRoot, StorageLocation, PathTemplate
- Placeholders: %datetime, %date, %time, %hostname, %m, %p, %s, %b, %a, %r
- RCSEnabled, RCSPort
- AutoStart
- RequiredStreams (warning only)
"""

import argparse
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional

from .recorder import LabRecorder


def _read_cfg_lines(path: str) -> List[str]:
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()


def _strip_comment(line: str) -> str:
    # Remove comments starting with ';' or '#'
    for sep in (';', '#'):
        if sep in line:
            # Only treat as comment if sep is at start or preceded by whitespace
            idx = line.find(sep)
            if idx == 0 or line[:idx].strip() != '':
                line = line[:idx]
            else:
                line = line
    return line.strip()


def _parse_value(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ''

    # Quoted comma-separated list: "a", "b"
    if '"' in s:
        items = [item.strip() for item in s.split(',')]
        cleaned: List[str] = []
        for item in items:
            if item.startswith('"') and item.endswith('"') and len(item) >= 2:
                cleaned.append(item[1:-1])
            else:
                # tolerate single quoted or unquoted tokens
                cleaned.append(item.strip('"').strip("'"))
        # If only one element, return string; otherwise list
        return cleaned if len(cleaned) != 1 else cleaned[0]

    # Numeric (int/float)
    if re.fullmatch(r"[-+]?\d+", s):
        try:
            return int(s)
        except Exception:
            pass
    if re.fullmatch(r"[-+]?\d*\.\d+", s):
        try:
            return float(s)
        except Exception:
            pass

    # Booleans encoded as 0/1
    if s in ('0', '1'):
        return int(s)

    return s


def parse_cfg_file(path: str) -> Dict[str, Any]:
    """Parse a LabRecorder .cfg file into a dict of key->value.

    The parser is permissive and ignores unknown lines and comments.
    """
    config: Dict[str, Any] = {}
    for raw in _read_cfg_lines(path):
        line = _strip_comment(raw)
        if not line:
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value_parsed = _parse_value(value)
        config[key] = value_parsed
    return config


def _utc_datetime_token() -> str:
    """Match C++ Qt format: yyyy-MM-ddTHHmmss.zzzZ (UTC)."""
    dt = datetime.now(timezone.utc)
    millis = int(dt.microsecond / 1000)
    return f"{dt.strftime('%Y-%m-%dT%H%M%S')}.{millis:03d}Z"

def _utc_date_token() -> str:
    """Match C++ Qt format: yyyy-MM-dd (UTC)."""
    dt = datetime.now(timezone.utc)
    return dt.strftime('%Y-%m-%d')

def _utc_time_token() -> str:
    """Match C++ Qt format: HHmmss.zzzZ (UTC)."""
    dt = datetime.now(timezone.utc)
    millis = int(dt.microsecond / 1000)
    return f"{dt.strftime('%H%M%S')}.{millis:03d}Z"


def expand_template(template: str, placeholders: Dict[str, str]) -> str:
    """Expand placeholders like %datetime, %hostname in a template string."""
    result = template
    for key, value in placeholders.items():
        result = result.replace(f'%{key}', value)
    return result


def build_filename_from_cfg(cfg: Dict[str, Any]) -> str:
    study_root = str(cfg.get('StudyRoot') or '').strip()
    storage_location = str(cfg.get('StorageLocation') or '').strip()
    path_template = str(cfg.get('PathTemplate') or '').strip()

    # Defaults for placeholders
    hostname = socket.gethostname()
    placeholders = {
        'datetime': _utc_datetime_token(),
        'date': _utc_date_token(),
        'time': _utc_time_token(),
        'hostname': hostname,
        'm': str(cfg.get('BidsModality') or 'eeg'),
        'p': str(cfg.get('Participant') or 'P001'),
        's': str(cfg.get('Session') or 'S001'),
        'b': str(cfg.get('Block') or 'task'),
        'a': str(cfg.get('Acq') or 'acq'),
        'r': str(cfg.get('Run') or '01'),
    }

    if storage_location:
        dest = expand_template(storage_location, placeholders)
    elif study_root and path_template:
        dest = os.path.join(study_root, expand_template(path_template, placeholders))
    elif study_root:
        # Only StudyRoot provided: fallback to a sensible default
        fname = f"LabRecorder_{hostname}_{placeholders['datetime']}_eeg.xdf"
        dest = os.path.join(study_root, fname)
    elif path_template:
        dest = expand_template(path_template, placeholders)
    else:
        dest = 'recording.xdf'

    # Ensure extension
    if not dest.lower().endswith('.xdf'):
        dest = f"{dest}.xdf"

    # Sanitize basename for Windows invalid filename characters
    def _sanitize_basename(name: str) -> str:
        # Remove characters not allowed on Windows
        name = re.sub(r'[<>:"/\\|?*]', '-', name)
        # Avoid trailing spaces or dots
        return name.rstrip(' .')

    dir_name, base_name = os.path.split(dest)
    base_name = _sanitize_basename(base_name)
    dest = os.path.join(dir_name, base_name)

    # Normalize and expand env/user
    dest = os.path.expandvars(os.path.expanduser(dest))
    return dest


def _parse_required_streams(cfg: Dict[str, Any]) -> List[str]:
    raw = cfg.get('RequiredStreams')
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def _format_stream_label(info: Any) -> str:
    try:
        # pylsl.StreamInfo has .name() and .hostname()
        return f"{info.name()} ({info.hostname()})"
    except Exception:
        return info.name()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def run_with_cfg(cfg_path: str) -> int:
    cfg = parse_cfg_file(cfg_path)

    filename = build_filename_from_cfg(cfg)
    _ensure_parent_dir(filename)

    rc_enabled_raw = cfg.get('RCSEnabled', 1)
    enable_remote = bool(int(rc_enabled_raw)) if isinstance(rc_enabled_raw, (int, str)) else True

    rc_port = cfg.get('RCSPort', 22345)
    try:
        rc_port = int(rc_port)
    except Exception:
        rc_port = 22345

    auto_start_raw = cfg.get('AutoStart', 0)
    auto_start = bool(int(auto_start_raw)) if isinstance(auto_start_raw, (int, str)) else False

    recorder = LabRecorder(
        filename=filename,
        enable_remote_control=enable_remote,
        remote_control_port=rc_port,
        config_file=None,
    )

    try:
        print("=== Lab Recorder Python (CFG) ===")
        print(f"Output file: {filename}")

        # Start remote control if enabled
        if enable_remote:
            if recorder.start_remote_control_server():
                print(f"Remote control enabled on port {rc_port}")
            else:
                print("Warning: Failed to start remote control server")

        # Discover streams
        print("\nDiscovering LSL streams...")
        available_streams = recorder.find_streams()

        # Required streams warning (if any)
        required_labels = _parse_required_streams(cfg)
        if required_labels:
            available_labels = {_format_stream_label(info) for info in available_streams}
            missing = [label for label in required_labels if label not in available_labels]
            if missing:
                print("Warning: Required streams not found:")
                for m in missing:
                    print(f"  - {m}")

        if available_streams:
            uids_to_record = [info.uid() for info in available_streams]
            recorder.select_streams_to_record(uids_to_record)
            print(f"\nSelected {len(uids_to_record)} streams for recording.")

            if auto_start or not enable_remote:
                print("\nStarting recording...")
                recorder.start_recording()
                print("Recording in progress. Press Ctrl+C to stop.")
                try:
                    while recorder.is_recording():
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    print("\nStopping recording...")
                    recorder.stop_recording()
            else:
                print("\nRecorder ready. Use remote control commands to start/stop recording.")
                print("Commands: select all|none, start, stop, update, filename <name>, status, streams")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nShutdown requested...")
        else:
            print("No LSL streams found.")
            if enable_remote and not auto_start:
                print("Remote control server is running. You can:")
                print("1. Start streams and use 'update' command")
                print("2. Use remote control to manage recording")
                print("Press Ctrl+C to exit.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nShutdown requested...")
            else:
                print("Exiting...")

    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        recorder.cleanup()
        print("Lab Recorder finished.")

    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Run Lab Recorder Python using a C++ .cfg file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Example: python -m labrecorder.cfg_loader -c App-LabRecorder/LabRecorder.cfg',
    )
    p.add_argument('-c', '--cfg', required=True, help='Path to LabRecorder .cfg file')
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    cfg_path = os.path.expanduser(os.path.expandvars(args.cfg))
    if not os.path.isfile(cfg_path):
        print(f"Config file not found: {cfg_path}")
        return 1
    return run_with_cfg(cfg_path)


if __name__ == '__main__':
    sys.exit(main())



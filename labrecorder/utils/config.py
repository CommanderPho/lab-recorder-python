"""
Configuration handling for Lab Recorder.
"""

import json
import os
import re
import socket
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List


def _read_cfg_lines(path: str) -> List[str]:
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()


def _strip_comment(line: str) -> str:
    # Remove comments starting with ';' or '#'
    for sep in (';', '#'):
        if sep in line:
            idx = line.find(sep)
            if idx >= 0:
                line = line[:idx]
    return line.strip()


def _parse_value(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ''
    if '"' in s:
        items = [item.strip() for item in s.split(',')]
        cleaned: List[str] = []
        for item in items:
            if item.startswith('"') and item.endswith('"') and len(item) >= 2:
                cleaned.append(item[1:-1])
            else:
                cleaned.append(item.strip('"').strip("'"))
        return cleaned if len(cleaned) != 1 else cleaned[0]
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
    if s in ('0', '1'):
        return int(s)
    return s


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


def _expand_template(template: str, placeholders: Dict[str, str]) -> str:
    result = template
    for key, value in placeholders.items():
        result = result.replace(f'%{key}', value)
    return result


def _sanitize_basename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    return name.rstrip(' .')


class Config:
    """Configuration manager for Lab Recorder."""
    
    DEFAULT_CONFIG = {
        'filename': 'recording.xdf',
        'remote_control': {
            'enabled': True,
            'port': 22345
        },
        'recording': {
            'buffer_size': 360,
            'max_samples_per_pull': 500,
            'clock_sync_interval': 5.0
        },
        'streams': {
            'timeout': 2.0,
            'recover': True
        }
    }
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration.
        
        Args:
            config_file: Path to configuration file (optional)
        """
        self.config = self.DEFAULT_CONFIG.copy()
        self.config_file = config_file
        
        if config_file and os.path.exists(config_file):
            self.load_from_file(config_file)
    
    def load_from_file(self, filename: str) -> None:
        """
        Load configuration from JSON (.json) or C++ style (.cfg) file.
        
        Args:
            filename: Path to configuration file
        """
        try:
            if filename.lower().endswith('.cfg'):
                self._load_from_cfg(filename)
            else:
                with open(filename, 'r') as f:
                    file_config = json.load(f)
                    self._deep_update(self.config, file_config)
        except Exception as e:
            print(f"Warning: Could not load config file {filename}: {e}")
    
    def save_to_file(self, filename: str) -> None:
        """
        Save current configuration to JSON file.
        
        Args:
            filename: Path to save configuration file
        """
        try:
            with open(filename, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config file {filename}: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value.
        
        Args:
            key: Configuration key (supports dot notation, e.g., 'remote_control.port')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self.config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value.
        
        Args:
            key: Configuration key (supports dot notation)
            value: Value to set
        """
        keys = key.split('.')
        config = self.config
        
        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        # Set the final key
        config[keys[-1]] = value
    
    def _deep_update(self, base_dict: Dict, update_dict: Dict) -> None:
        """
        Deep update of nested dictionaries.
        
        Args:
            base_dict: Base dictionary to update
            update_dict: Dictionary with updates
        """
        for key, value in update_dict.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._deep_update(base_dict[key], value)
            else:
                base_dict[key] = value 

    # === .cfg support ===
    def _load_from_cfg(self, cfg_path: str) -> None:
        """
        Load settings from a LabRecorder .cfg file and map to Python config.
        """
        cfg: Dict[str, Any] = {}
        for raw in _read_cfg_lines(cfg_path):
            line = _strip_comment(raw)
            if not line or '=' not in line:
                continue
            key, value = line.split('=', 1)
            cfg[key.strip()] = _parse_value(value)

        # Build filename from StudyRoot/StorageLocation/PathTemplate
        study_root = str(cfg.get('StudyRoot') or '').strip()
        storage_location = str(cfg.get('StorageLocation') or '').strip()
        path_template = str(cfg.get('PathTemplate') or '').strip()

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
            dest = _expand_template(storage_location, placeholders)
        elif study_root and path_template:
            dest = os.path.join(study_root, _expand_template(path_template, placeholders))
        elif study_root:
            fname = f"LabRecorder_{hostname}_{placeholders['datetime']}_eeg.xdf"
            dest = os.path.join(study_root, fname)
        elif path_template:
            dest = _expand_template(path_template, placeholders)
        else:
            dest = self.config.get('filename', 'recording.xdf')

        if not dest.lower().endswith('.xdf'):
            dest = f"{dest}.xdf"

        dir_name, base_name = os.path.split(dest)
        base_name = _sanitize_basename(base_name)
        dest = os.path.join(dir_name, base_name)
        dest = os.path.expandvars(os.path.expanduser(dest))

        self.set('filename', dest)

        # Remote control settings
        rc_enabled_raw = cfg.get('RCSEnabled')
        if rc_enabled_raw is not None:
            try:
                self.set('remote_control.enabled', bool(int(rc_enabled_raw)))
            except Exception:
                pass
        rc_port_raw = cfg.get('RCSPort')
        if rc_port_raw is not None:
            try:
                self.set('remote_control.port', int(rc_port_raw))
            except Exception:
                pass

        # Auto start (stored but not acted on here)
        auto_start_raw = cfg.get('AutoStart')
        if auto_start_raw is not None:
            try:
                self.set('auto_start', bool(int(auto_start_raw)))
            except Exception:
                pass

        # Required streams (warning responsibility remains with caller)
        required = cfg.get('RequiredStreams')
        if required is not None:
            if not isinstance(required, list):
                required_list = [str(required)]
            else:
                required_list = [str(x) for x in required]
            streams_cfg = self.config.get('streams', {})
            streams_cfg['required_labels'] = required_list
            self.config['streams'] = streams_cfg
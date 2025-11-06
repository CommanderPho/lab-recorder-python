"""
Main Lab Recorder class.
"""

import time
import threading
import pylsl
from typing import Optional, Dict, List

from .streams import StreamManager, AcquisitionManager
from .xdf import SimpleXDFWriter
from .remote_control import RemoteControlServer
from .utils import Config


class LabRecorder:
    """
    Main Lab Recorder class that coordinates all recording functionality.
    """
    
    def __init__(self, filename: str = "recording.xdf", 
                 enable_remote_control: bool = True,
                 remote_control_port: int = 22345,
                 config_file: Optional[str] = None):
        """
        Initialize Lab Recorder.
        
        Args:
            filename: Output XDF filename
            enable_remote_control: Enable TCP remote control interface
            remote_control_port: Port for remote control server
            config_file: Optional configuration file path
        """
        # Load configuration
        self.config = Config(config_file)
        
        # Recording state
        self.filename = filename
        self.is_recording_flag = False
        self.xdf_writer: Optional[SimpleXDFWriter] = None
        
        # Stream management
        self.stream_manager = StreamManager()
        self.acquisition_manager = AcquisitionManager(self._on_data_received)
        
        # Data buffers and synchronization
        self._data_buffers = {}  # uid -> list of (samples, timestamps)
        self.buffer_lock = threading.Lock()
        self.writer_thread: Optional[threading.Thread] = None
        
        # Resolve remote control from config (if provided)
        cfg_enable = self.config.get('remote_control.enabled', None)
        cfg_port = self.config.get('remote_control.port', None)
        final_enable_remote = bool(cfg_enable) if cfg_enable is not None else enable_remote_control
        final_remote_port = int(cfg_port) if cfg_port is not None else remote_control_port

        # Prefer config-provided filename if caller used default
        cfg_filename = self.config.get('filename', None)
        if (filename is None or filename == "recording.xdf") and cfg_filename:
            self.filename = cfg_filename
        
        # Remote control
        self.remote_control_server: Optional[RemoteControlServer] = None
        if final_enable_remote:
            self.remote_control_server = RemoteControlServer(self, final_remote_port)
        
        # Stream recording state
        self.stream_inlets = {}  # uid -> inlet
        self.stream_ids = {}     # uid -> xdf_stream_id
        
    def find_streams(self, timeout: float = 2.0) -> List[pylsl.StreamInfo]:
        """
        Discover available LSL streams.
        
        Args:
            timeout: Discovery timeout in seconds
            
        Returns:
            List of discovered stream info objects
        """
        return self.stream_manager.find_streams(timeout)
    
    def select_streams_to_record(self, stream_uids: List[str]) -> None:
        """
        Select streams for recording.
        
        Args:
            stream_uids: List of stream UIDs to select
        """
        self.stream_manager.select_streams(stream_uids)
    
    def start_recording(self) -> None:
        """Start the recording process."""
        if self.is_recording_flag:
            raise RuntimeError("Recording is already in progress")
        
        selected_streams = self.stream_manager.get_selected_streams()
        if not selected_streams:
            raise RuntimeError("No streams selected for recording")
        
        # Ensure output directory exists
        try:
            import os
            out_dir = os.path.dirname(self.filename)
            if out_dir and not os.path.isdir(out_dir):
                os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create output directory for {self.filename}: {e}")

        # Initialize XDF writer
        self.xdf_writer = SimpleXDFWriter(self.filename)
        self.xdf_writer.open()
        print(f"XDF file {self.filename} opened for writing.")
        
        # Set up streams for recording
        self._setup_recording_streams(selected_streams)
        
        # Start data acquisition
        self.acquisition_manager.start_all()
        
        # Start writer thread
        self.is_recording_flag = True
        self.writer_thread = threading.Thread(target=self._writer_thread_func, daemon=True)
        self.writer_thread.start()
        
        print(f"Recording started for {len(selected_streams)} streams.")
    
    def stop_recording(self) -> None:
        """Stop the recording process."""
        if not self.is_recording_flag:
            return
        
        print("Stopping recording...")
        self.is_recording_flag = False
        
        # Stop data acquisition
        self.acquisition_manager.stop_all()
        
        # Wait for writer thread to finish
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=5.0)
        
        # Close LSL inlets
        for uid, inlet in self.stream_inlets.items():
            try:
                inlet.close_stream()
                stream_name = self.stream_manager.get_stream_info(uid).name()
                print(f"Closed LSL inlet for stream {stream_name}.")
            except Exception as e:
                print(f"Error closing inlet for stream {uid}: {e}")
        
        # Close XDF file
        if self.xdf_writer:
            self.xdf_writer.close()
            self.xdf_writer = None
        
        # Clean up
        self.stream_inlets.clear()
        self.stream_ids.clear()
        self._data_buffers.clear()
        
        print(f"Recording saved to {self.filename}")
    
    def start_remote_control_server(self) -> bool:
        """
        Start the remote control server.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self.remote_control_server:
            return self.remote_control_server.start()
        return False
    
    def stop_remote_control_server(self) -> None:
        """Stop the remote control server."""
        if self.remote_control_server:
            self.remote_control_server.stop()
    
    def cleanup(self) -> None:
        """Clean up all resources."""
        if self.is_recording_flag:
            self.stop_recording()
        self.stop_remote_control_server()
    
    # Remote control interface methods
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.is_recording_flag
    
    def has_selected_streams(self) -> bool:
        """Check if any streams are selected."""
        return bool(self.stream_manager.selected_stream_uids)
    
    def select_all_streams(self) -> int:
        """Select all discovered streams."""
        return self.stream_manager.select_all_streams()
    
    def deselect_all_streams(self) -> None:
        """Deselect all streams."""
        self.stream_manager.deselect_all_streams()
    
    def update_streams(self) -> int:
        """Update available streams list."""
        streams = self.find_streams()
        return len(streams)
    
    def set_filename(self, filename: str) -> None:
        """Set recording filename."""
        if self.is_recording_flag:
            raise RuntimeError("Cannot change filename while recording")
        self.filename = filename
    
    def get_status(self) -> Dict:
        """Get current recorder status."""
        return {
            'recording': self.is_recording_flag,
            'filename': self.filename,
            'selected_streams': len(self.stream_manager.selected_stream_uids),
            'available_streams': len(self.stream_manager.discovered_streams)
        }
    
    def get_stream_list(self) -> List[Dict]:
        """Get list of available streams."""
        return self.stream_manager.get_stream_list()
    
    # Private methods
    def _setup_recording_streams(self, selected_streams: Dict[str, pylsl.StreamInfo]) -> None:
        """Set up streams for recording."""
        for uid, stream_info in selected_streams.items():
            try:
                # Create inlet
                inlet = pylsl.StreamInlet(
                    stream_info, 
                    max_buflen=self.config.get('recording.buffer_size', 360),
                    recover=self.config.get('streams.recover', True)
                )
                
                print(f"Opened LSL inlet for stream: {stream_info.name()} (UID: {uid})")
                
                # Get initial timestamp for synchronization
                first_sample, first_timestamp_lsl = inlet.pull_sample(timeout=1.0)
                first_timestamp_local = pylsl.local_clock()
                
                if first_timestamp_lsl is not None:
                    # Store initial offset for clock synchronization
                    initial_offset = first_timestamp_lsl - first_timestamp_local
                    
                    print(f"Stream {stream_info.name()}: Initial LSL ts: {first_timestamp_lsl:.4f}, "
                          f"Local ts: {first_timestamp_local:.4f}, Offset: {initial_offset:.4f}")
                    
                    # Pre-fill buffer with first sample
                    if first_sample:
                        with self.buffer_lock:
                            self._data_buffers.setdefault(uid, []).append(
                                ([first_sample], [first_timestamp_lsl])
                            )
                
                # Add stream to XDF
                xdf_stream_id = self.xdf_writer.add_stream(stream_info)
                print(f"Added stream {stream_info.name()} to XDF with ID {xdf_stream_id}")
                
                # Write initial clock offset
                if first_timestamp_lsl is not None:
                    self.xdf_writer.write_clock_offset(
                        xdf_stream_id, first_timestamp_local, initial_offset
                    )
                    print(f"Written initial clock offset for {stream_info.name()}.")
                
                # Store references
                self.stream_inlets[uid] = inlet
                self.stream_ids[uid] = xdf_stream_id
                
                # Add to acquisition manager
                self.acquisition_manager.add_stream(uid, stream_info, inlet)
                
            except Exception as e:
                print(f"Failed to set up stream {stream_info.name()} (UID: {uid}): {e}")
                # Clean up on failure
                if self.xdf_writer:
                    self.xdf_writer.close()
                    self.xdf_writer = None
                raise
    
    def _on_data_received(self, stream_uid: str, samples: List, timestamps: List) -> None:
        """Callback for when data is received from acquisition threads."""
        with self.buffer_lock:
            self._data_buffers.setdefault(stream_uid, []).append((samples, timestamps))
    
    def _writer_thread_func(self) -> None:
        """Writer thread that writes buffered data to XDF file."""
        print("XDF Writer thread started.")
        
        while self.is_recording_flag or any(self._data_buffers.values()):
            data_written = False
            
            # Process data for each stream
            for uid, buffer in list(self._data_buffers.items()):
                chunks_to_write = []
                
                with self.buffer_lock:
                    if buffer:
                        chunks_to_write.extend(buffer)
                        buffer.clear()
                
                # Write chunks to XDF
                if chunks_to_write:
                    data_written = True
                    for samples_chunk, timestamps_chunk in chunks_to_write:
                        try:
                            self.xdf_writer.write_samples(uid, samples_chunk, timestamps_chunk)
                        except Exception as e:
                            print(f"Error writing samples for stream UID {uid} to XDF: {e}")
            
            # Sleep if no data was written
            if not data_written:
                if self.is_recording_flag:
                    time.sleep(0.05)
                elif not any(self._data_buffers.values()):
                    break
        
        print("XDF Writer thread finished.") 
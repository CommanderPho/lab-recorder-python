#!/usr/bin/env python3
"""
Lab Recorder Python - Main Entry Point

A cross-platform Python implementation of Lab Streaming Layer (LSL) recorder
with remote control capabilities.
"""

import sys
import time
import argparse
from labrecorder import LabRecorder


def main():
    """Main entry point for Lab Recorder."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Lab Streaming Layer (LSL) Recorder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Start with default settings
  %(prog)s -f my_recording.xdf               # Specify output filename
  %(prog)s --no-remote                       # Disable remote control
  %(prog)s -p 12345                          # Use custom remote control port
  %(prog)s --config config.json              # Use configuration file
        """
    )
    
    parser.add_argument(
        '--filename', '-f', 
        default="recording.xdf", 
        help='Output XDF filename (default: recording.xdf)'
    )
    
    parser.add_argument(
        '--remote-control', '-r', 
        action='store_true', 
        default=True,
        help='Enable remote control interface (default: enabled)'
    )
    
    parser.add_argument(
        '--port', '-p', 
        type=int, 
        default=22345,
        help='Remote control port (default: 22345)'
    )
    
    parser.add_argument(
        '--no-remote', 
        action='store_true',
        help='Disable remote control interface'
    )
    
    parser.add_argument(
        '--config', '-c',
        help='Configuration file path'
    )
    
    args = parser.parse_args()
    
    # Determine remote control setting
    enable_remote = args.remote_control and not args.no_remote
    
    # Create recorder instance
    recorder = LabRecorder(
        filename=args.filename, 
        enable_remote_control=enable_remote,
        remote_control_port=args.port,
        config_file=args.config
    )

    # Reflect any overrides from configuration (.cfg or JSON)
    cfg_enabled = recorder.config.get('remote_control.enabled', None)
    if cfg_enabled is not None:
        enable_remote = bool(cfg_enabled)
    cfg_port = recorder.config.get('remote_control.port', None)
    if cfg_port is not None:
        try:
            args.port = int(cfg_port)
        except Exception:
            pass
    auto_start = bool(recorder.config.get('auto_start', False))
    
    try:
        print("=== Lab Recorder Python ===")
        print(f"Output file: {recorder.filename}")
        
        # Start remote control server if enabled
        if enable_remote:
            if recorder.start_remote_control_server():
                print(f"Remote control enabled on port {args.port}")
                print("Commands: select all|none, start, stop, update, filename <name>, status, streams")
            else:
                print("Warning: Failed to start remote control server")
        
        # Discover available streams
        print("\nDiscovering LSL streams...")
        available_streams = recorder.find_streams()
        
        if available_streams:
            # Select all streams by default
            uids_to_record = [info.uid() for info in available_streams]
            recorder.select_streams_to_record(uids_to_record)
            
            print(f"\nSelected {len(uids_to_record)} streams for recording.")
            
            if enable_remote and not auto_start:
                print("\nRecorder ready. Use remote control commands to start/stop recording.")
                print("Or press Ctrl+C to exit.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nShutdown requested...")
            else:
                # Start recording immediately if no remote control
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
        # Clean up resources
        recorder.cleanup()
        print("Lab Recorder finished.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 
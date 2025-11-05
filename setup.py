#!/usr/bin/env python3
"""
Setup script for Lab Recorder Python
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="lab-recorder-python",
    version="1.0.0",
    author="Yahmin Haj",
    description="A cross-platform Python implementation of Lab Streaming Layer (LSL) recorder",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/CommanderPho/lab-recorder-python",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering",
        "Topic :: System :: Networking",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "lab-recorder=main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "labrecorder": ["*.py"],
    },
) 
#!/usr/bin/env python3
"""
IntelliAide Setup Script

A comprehensive must-gather analysis system for OpenShift diagnostics.
"""

from setuptools import setup, find_packages
import os

# Read the contents of requirements.txt
def read_requirements():
    req_path = os.path.join('src', 'api', 'requirements.txt')
    if os.path.exists(req_path):
        with open(req_path, 'r') as f:
            # Filter out comments and empty lines
            requirements = []
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Extract package name without version constraints for setup.py
                    requirements.append(line)
            return requirements
    return []

# Read the contents of README.md
def read_readme():
    with open('README.md', 'r', encoding='utf-8') as f:
        return f.read()

setup(
    name="intelliaide",
    version="3.0.0",
    author="IntelliAide Team",
    description="A comprehensive must-gather analysis system for OpenShift diagnostics",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/your-org/intelliaide",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "Topic :: System :: Systems Administration",
        "Topic :: Software Development :: Debuggers",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
    extras_require={
        'dev': [
            'pytest>=6.0',
            'pytest-cov>=2.0',
            'black>=21.0',
            'flake8>=3.8',
            'mypy>=0.800',
        ],
    },
    entry_points={
        'console_scripts': [
            'intelliaide-api=api.api:main',
            'intelliaide-worker=api.worker:main',
            'intelliaide-chat=api.chat_ui:main',
            'intelliaide-hydra=api.hydra_client:main',
        ],
    },
    package_data={
        'api': [
            'config/*.json',
            'config/*.yaml', 
            'data_source/*.md',
            'data_source/*.odt',
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
from setuptools import setup, find_packages

setup(
    name="iceberg-maintenance-monitor",
    version="0.2.0",
    description="AI-powered Apache Iceberg table maintenance agent",
    author="Your Name",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "pyiceberg>=0.7.0",
        "boto3>=1.28.0",
        "click>=8.1.0",
        "pyyaml>=6.0",
        "pandas>=2.0.0",
        "pyarrow>=12.0.0",
        "anthropic>=0.40.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "hypothesis>=6.82.0",
            "moto>=4.1.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "iceberg-monitor=cli:main",
        ],
    },
    python_requires=">=3.10",
)

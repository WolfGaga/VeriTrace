from setuptools import find_packages, setup

setup(
    name="veritrace-deploy",
    version="0.2.0",
    description="VERITRACE Docker Launcher",
    packages=find_packages(),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "veritrace-deploy=veritrace_deploy.__main__:main",
        ],
    },
)

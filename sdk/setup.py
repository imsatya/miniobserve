from pathlib import Path
from setuptools import setup, find_packages

long_description = (Path(__file__).parent / "README.md").read_text(encoding="utf-8")

setup(
    name="miniobserve",
    version="0.1.4",
    description="Lightweight LLM observability for indie developers and small teams",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/miniobserve/miniobserve",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=["httpx>=0.24.0"],
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    entry_points={
        "console_scripts": [
            "miniobserve=miniobserve.cli:main",
        ]
    },
    extras_require={
        "langchain": ["langchain-core>=0.2.0"],
        "openai": ["langchain-core>=0.2.0", "langchain-openai>=0.1.0"],
        "anthropic": ["langchain-core>=0.2.0", "langchain-anthropic>=0.1.0"],
    },
)

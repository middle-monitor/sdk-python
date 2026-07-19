from setuptools import setup, find_packages

setup(
    name="middle-monitor-sdk",
    version="0.1.5",
    description="Python SDK for Middle-Monitor error reporting with OpenTelemetry",
    author="Middle-Monitor",
    license="MIT",
    url="https://github.com/middle-monitor/sdk-python",
    packages=find_packages(),
    install_requires=[
        "opentelemetry-api>=1.24.0",
        "opentelemetry-sdk>=1.24.0",
        "opentelemetry-exporter-otlp-proto-http>=1.24.0",
        "opentelemetry-semantic-conventions>=0.48b0",
        "opentelemetry-instrumentation>=0.48b0",
    ],
    python_requires=">=3.8",
)


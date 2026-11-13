FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# Pin uv so Codespaces and local development use a reproducible toolchain.
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/

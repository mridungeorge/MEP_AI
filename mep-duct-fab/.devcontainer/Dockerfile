FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The base image ships a yarn apt source whose signing key has expired; it breaks `apt-get update`
# in the features that run after this image (docker-in-docker). We do not use yarn.
RUN rm -f /etc/apt/sources.list.d/yarn.list

FROM ubuntu:22.04

RUN \
  set -eux \
  && echo 'APT::Install-Recommends "false";' >/etc/apt/apt.conf.d/00recommends \
  && echo 'APT::Install-Suggests "false";' >>/etc/apt/apt.conf.d/00recommends \
  && echo 'APT::Get::Install-Recommends "false";' >>/etc/apt/apt.conf.d/00recommends \
  && echo 'APT::Get::Install-Suggests "false";' >>/etc/apt/apt.conf.d/00recommends

RUN apt update && \
    apt install -y wireguard-tools iproute2 iptables iputils-ping curl openssh-client && \
    apt-get -y clean && \
    rm -rf /var/lib/apt/lists/*

ADD scripts/* /scripts/
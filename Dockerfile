FROM ubuntu:22.04

RUN apt update && \
    apt install -y wireguard-tools iproute2 iptables iputils-ping curl openssh-client

ADD scripts/* /scripts/
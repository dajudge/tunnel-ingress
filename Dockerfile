FROM ubuntu:22.04

RUN apt update && \
    apt install -y wireguard-tools

RUN apt update && \
    apt install -y iproute2 iptables net-tools iputils-ping curl

RUN apt update && \
    apt install -y openssh-client

ADD external-setup.sh /
ADD run.sh /

CMD ["/run.sh"]
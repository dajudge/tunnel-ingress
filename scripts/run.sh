#! /bin/bash
set -eu

echo "Config:"
echo "  INTERNAL_PRIVATE_IP: ${INTERNAL_PRIVATE_IP}"
echo "  EXTERNAL_PRIVATE_IP: ${EXTERNAL_PRIVATE_IP}"
echo "  HOST: ${HOST}"
echo "  PORTS: ${PORTS}"

echo "Import SSH key..."
mkdir -p /root/.ssh/
cp /ssh-key/* /root/.ssh/
chmod 0600 /root/.ssh -R


rm -rf /tmp/wg/
mkdir -p /tmp/wg/
wg genkey > /tmp/wg/private-key
wg pubkey < /tmp/wg/private-key > /tmp/wg/public-key
INTERNAL_PUBKEY="$(cat /tmp/wg/public-key)"

function remote() {
  ssh -o "StrictHostKeyChecking=no" -l root $HOST "$@"
}

scp -o "StrictHostKeyChecking=no" /scripts/external-setup.sh root@$HOST:/tmp/external-setup.sh
remote "PORTS=\"${PORTS}\" INTERNAL_PRIVATE_IP=\"${INTERNAL_PRIVATE_IP}\" EXTERNAL_PRIVATE_IP=\"${EXTERNAL_PRIVATE_IP}\" INTERNAL_PUBKEY=\"${INTERNAL_PUBKEY}\" /tmp/external-setup.sh"

EXTERNAL_PUBKEY="$(remote "cat /tmp/wg/public-key")"

echo "Configuring internal wireguard..."
if [ ! -f "/var/run/tunnel-ingress/internal_wireguard_configured" ]; then
  echo "Creating wireguard interface..."
  ip link add dev wg0 type wireguard
  ip address add dev wg0 "${INTERNAL_PRIVATE_IP}/24"
  touch /var/run/tunnel-ingress/internal_wireguard_configured
else
  echo "Wireguard interface already existing"
fi
wg set wg0 listen-port 51820
wg set wg0 private-key /tmp/wg/private-key
wg set wg0 peer "$EXTERNAL_PUBKEY" allowed-ips "${EXTERNAL_PRIVATE_IP}/32" endpoint "${HOST}:51820" persistent-keepalive 5
ip link set up dev wg0

POD_IFACE="eth0"
POD_IP="$(ip -4 addr show "${POD_IFACE}" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')"

echo "Configuring internal iptables..."
for PORT in $(echo "${PORTS}" | tr ',' '\n'); do
  PORT_INPUT="$(echo "${PORT}" | awk -F ':' '{print $1}')"
  IP_OUTPUT="$(echo "${PORT}" | awk -F ':' '{print $2}')"
  PORT_OUTPUT="$(echo "${PORT}" | awk -F ':' '{print $3}')"
  PROTOCOL="$(echo "${PORT}" | awk -F ':' '{print $4}')"
  echo "${PROTOCOL} ${PORT_INPUT} --> ${IP_OUTPUT}:${PORT_OUTPUT}"
  iptables -t nat -A PREROUTING -p ${PROTOCOL} --dport "${PORT_INPUT}" -j DNAT --to-destination "${IP_OUTPUT}:${PORT_OUTPUT}"
  iptables -t nat -A POSTROUTING -p ${PROTOCOL} -d "${IP_OUTPUT}" --dport "${PORT_OUTPUT}" -j SNAT --to-source "${POD_IP}"
done

remote "while :; do date; curl '${PING_URL}' || true; echo ""; sleep 60; done"
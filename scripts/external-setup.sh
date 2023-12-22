#! /bin/sh
set -eu

echo "Installing external wireguard..."
which wg > /dev/null 2>&1 || (apt update && apt install -y wireguard-tools)
which ip > /dev/null 2>&1 || (apt update && apt install -y iproute2)
which iptables > /dev/null 2>&1 || (apt update && apt install -y iptables)

echo "Resetting external network config..."
for i in $(wg show wg0 | grep 'peer:' | awk '{print $2}'); do
  echo "Removing peer $i..."
  wg set wg0 peer $i remove
done
ip link show wg0 > /dev/null 2>&1 && (echo "Deleting external wg0 interface..." ; ip link delete dev wg0)

echo "Configuring external wireguard..."
rm -rf /tmp/wg/
mkdir -p /tmp/wg/
chmod 600 /tmp/wg/
wg genkey > /tmp/wg/private-key
wg pubkey < /tmp/wg/private-key > /tmp/wg/public-key
ip link add dev wg0 type wireguard
ip address add dev wg0 "${EXTERNAL_PRIVATE_IP}/24"
wg set wg0 listen-port 51820
wg set wg0 private-key /tmp/wg/private-key
wg set wg0 peer $INTERNAL_PUBKEY allowed-ips ${INTERNAL_PRIVATE_IP}/32
ip link set up dev wg0

echo "Cleaning up external iptables..."
iptables -t nat -F TUNNEL-PREROUTING 2> /dev/null || true
iptables -t nat -F TUNNEL-POSTROUTING 2> /dev/null || true
iptables -t nat -D PREROUTING -j TUNNEL-PREROUTING 2> /dev/null || true
iptables -t nat -D POSTROUTING -j TUNNEL-POSTROUTING 2> /dev/null || true
iptables -t nat -X TUNNEL-PREROUTING 2> /dev/null || true
iptables -t nat -X TUNNEL-POSTROUTING 2> /dev/null || true

echo "Configuring external iptables..."
iptables -t nat -N TUNNEL-PREROUTING
iptables -t nat -N TUNNEL-POSTROUTING

for PORT in $(echo "${PORTS}" | tr ',' '\n'); do
  INGRESS_PORT="$(echo "${PORT}" | awk -F ':' '{print $1}')"
  PROTOCOL="$(echo "${PORT}" | awk -F ':' '{print $4}')"
  echo "${PROTOCOL} ${INGRESS_PORT} -> ${EXTERNAL_PRIVATE_IP}:${INGRESS_PORT}"
  iptables -t nat -A TUNNEL-PREROUTING -p ${PROTOCOL} --dport "${INGRESS_PORT}" -j DNAT --to-destination "${INTERNAL_PRIVATE_IP}:${INGRESS_PORT}"
  iptables -t nat -A TUNNEL-POSTROUTING -p ${PROTOCOL} -d "${INTERNAL_PRIVATE_IP}" --dport "${INGRESS_PORT}" -j SNAT --to-source "${EXTERNAL_PRIVATE_IP}"
done

iptables -t nat -A TUNNEL-PREROUTING -j RETURN
iptables -t nat -A TUNNEL-POSTROUTING -j RETURN
iptables -t nat -I PREROUTING -j TUNNEL-PREROUTING
iptables -t nat -I POSTROUTING -j TUNNEL-POSTROUTING

echo "Enabling external ip forwarding..."
echo 1 > /proc/sys/net/ipv4/ip_forward

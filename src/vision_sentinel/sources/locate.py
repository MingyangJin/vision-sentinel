"""Find a camera by MAC address.

The IP is a DHCP lease and can move - it did, from .44 to .20, mid-project.
The MAC is the camera's stable identity, so configuration references that and
the address is resolved at runtime. A DHCP reservation is still worth setting;
this is the layer that survives the reservation not being honoured.
"""

import concurrent.futures as cf
import ipaddress
import re
import socket
import subprocess


def normalise(mac: str) -> str:
    """macOS prints 'e:c7:1d:...' where the camera reports '0e:c7:1d:...'."""
    return ":".join(f"{int(o, 16):02x}" for o in mac.split(":"))


def arp_table() -> dict[str, str]:
    """Current ARP cache as {normalised_mac: ip}."""
    out = subprocess.run(["arp", "-an"], capture_output=True, text=True).stdout
    table = {}
    for ip, mac in re.findall(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+)", out):
        if "incomplete" not in mac:
            table[normalise(mac)] = ip
    return table


# Refuse to enumerate anything larger than this. Guards against picking up a
# loopback or tunnel interface and trying to scan millions of addresses.
MAX_HOSTS = 65536


def _default_interface() -> str | None:
    out = subprocess.run(["route", "-n", "get", "default"],
                         capture_output=True, text=True).stdout
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else None


def _local_network() -> ipaddress.IPv4Network | None:
    """Network of the interface carrying the default route.

    Must be that interface specifically: parsing bare `ifconfig` picks up lo0
    first, and 127.0.0.1/8 is 16.7 million addresses.
    """
    iface = _default_interface()
    if not iface:
        return None
    out = subprocess.run(["ifconfig", iface], capture_output=True, text=True).stdout
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", out)
    if not m:
        return None
    ip, mask = m.group(1), int(m.group(2), 16)
    dotted = ".".join(str((mask >> s) & 0xFF) for s in (24, 16, 8, 0))
    net = ipaddress.IPv4Network(f"{ip}/{dotted}", strict=False)
    return net if net.num_addresses <= MAX_HOSTS else None


def _port_open(ip: str, port: int, timeout: float) -> str | None:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return ip
    except OSError:
        return None
    finally:
        s.close()


def find(mac: str, port: int = 554, timeout: float = 1.0) -> str | None:
    """Resolve a MAC to its current IP, scanning the subnet if the cache misses.

    Scans by RTSP port rather than pinging every host: far fewer probes, and it
    only touches hosts that could plausibly be the camera.
    """
    want = normalise(mac)
    if ip := arp_table().get(want):
        return ip

    net = _local_network()
    if net is None:
        return None

    hosts = [str(h) for h in net.hosts()]
    with cf.ThreadPoolExecutor(128) as ex:
        candidates = [r for r in ex.map(lambda h: _port_open(h, port, timeout), hosts) if r]

    table = arp_table()
    for ip in candidates:
        if table.get(want) == ip:
            return ip
    return None

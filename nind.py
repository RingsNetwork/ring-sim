import argparse
import logging
import pathlib
import uuid

logger = logging.getLogger("nind")
base_dir = pathlib.Path(__file__).parent

try:
    from python_on_whales import docker
except ImportError:
    logger.error("Please install python-on-whales")
    exit(1)


def init_logger(level):
    _print_to_stderr = logging.StreamHandler()
    _print_to_stderr.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    logger = logging.getLogger("nind")
    logger.addHandler(_print_to_stderr)
    logger.setLevel(level)


def parse_args():
    parser = argparse.ArgumentParser(
        description="NIND(Node in Docker). Create node and configure NAT"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Set logging level to DEBUG",
    )

    subparsers = parser.add_subparsers(dest="cmd", required=True)

    build_image = subparsers.add_parser(
        "build_image", help="Build images for node and router"
    )
    build_image.add_argument(
        "-p",
        "--path",
        type=pathlib.Path,
        default="./docker",
        help="Path to the base directory contained build directories",
    )
    build_image.add_argument(
        "--builder",
        action="store_true",
        help="Export builder image for debug mode (it's super huge)",
    )

    create_nat = subparsers.add_parser("create_nat", help="Create a NAT")
    create_nat.add_argument(
        "--router-image",
        type=str,
        default="bnsnet/router",
        help="Image for router container",
    )
    create_nat.add_argument(
        "-n",
        "--name",
        type=str,
        help="Name for router container",
    )
    create_nat.add_argument(
        "-l",
        "--lan",
        type=str,
        help="NATed network",
    )
    create_nat.add_argument(
        "-w",
        "--wan",
        type=str,
        default="bridge",
        help="Outer network",
    )

    create_node = subparsers.add_parser("create_node", help="Create a node")
    create_node.add_argument(
        "-l",
        "--lan",
        type=str,
        required=True,
        help="NATed network",
    )
    create_node.add_argument(
        "-r",
        "--router",
        type=str,
        required=True,
        help="Router container",
    )
    create_node.add_argument(
        "--node-image",
        type=str,
        default="bnsnet/node",
        help="Image for node container",
    )
    create_node.add_argument(
        "-n",
        "--name",
        type=str,
        help="Name for node container",
    )
    create_node.add_argument(
        "-s",
        "--stun",
        type=str,
        required=True,
        help="STUN server url",
    )
    create_node.add_argument(
        "-k",
        "--key",
        type=str,
        help="ETH key",
    )
    create_node.add_argument(
        "--debug",
        action="store_true",
        help="Run with volumed codes, so that you can restart container to update running codes",
    )

    subparsers.add_parser("clean", help="Clean up all containers and networks")

    return parser.parse_args()


def nonce():
    return uuid.uuid4().hex[-8:]


def get_mac_ifname(container, mac):
    cmd = "ip -br link | awk '$3 ~ /'{mac}'/ {{print $1}}'".format(mac=mac)
    output = container.execute(["bash", "-c", cmd])
    return output.split("@")[0]


def build_image(args):

    if args.builder:
        p = args.path
        logger.info(f"Building image, path: {p}")
        docker.build(
            context_path=p, tags=["bnsnet/node-builder"], load=True, target="builder"
        )
        return

    p = args.path / "bns-router"
    logger.info(f"Building image, path: {p}")
    docker.build(context_path=p, tags=["bnsnet/router"], load=True)

    p = args.path
    logger.info(f"Building image, path: {p}")
    docker.build(context_path=p, tags=["bnsnet/node"], load=True)


def create_nat(args):
    wan_nw = docker.network.list(filters={"name": args.wan})[0]

    if args.lan is None:
        lan_nw = docker.network.create(
            f"bns-nw-{nonce()}",
            labels={"operator": "nind"},
        )
    else:
        lan_nw = docker.network.list(names=[args.lan])[0]

    if args.name is None:
        args.name = f"bns-router-{nonce()}"

    router = docker.container.create(
        args.router_image,
        name=args.name,
        cap_add=["NET_ADMIN"],
        networks=[lan_nw],
        labels={"operator": "nind"},
    )
    docker.network.connect(wan_nw, router)
    router.start()
    router.reload()

    wan_ip = router.network_settings.networks[wan_nw.name].ip_address
    wan_mac = router.network_settings.networks[wan_nw.name].mac_address
    wan_ifname = get_mac_ifname(router, wan_mac)

    lan_ip = router.network_settings.networks[lan_nw.name].ip_address
    lan_mac = router.network_settings.networks[lan_nw.name].mac_address
    lan_ifname = get_mac_ifname(router, lan_mac)

    logger.info(f"Router Container ID {router.id}")
    logger.info(f"(wan {wan_nw.name}) Ifname {wan_ifname}")
    logger.info(f"(wan {wan_nw.name}) IP Address {wan_ip}")
    logger.info(f"(wan {wan_nw.name}) Mac Address {wan_mac}")
    logger.info(f"(lan {lan_nw.name}) Ifname {lan_ifname}")
    logger.info(f"(lan {lan_nw.name}) IP Address {lan_ip}")
    logger.info(f"(lan {lan_nw.name}) Mac Address {lan_mac}")
    logger.info("Configuring iptables...")

    lan_subnet = lan_nw.ipam.config[0]["Subnet"]
    router.execute(
        [
            "iptables-legacy",
            "-t",
            "nat",
            "-A",
            "POSTROUTING",
            "-s",
            lan_subnet,
            "-o",
            wan_ifname,
            "-j",
            "SNAT",
            "--to-source",
            wan_ip,
        ]
    )

    print(f"-l {lan_nw.name} -r {router.name}")


def create_node(args):
    router = docker.container.list(filters={"name": args.router})[0]
    lan_nw = docker.network.list(filters={"name": args.lan})[0]

    wan_nw_id = next(
        v.network_id
        for v in router.network_settings.networks.values()
        if v.network_id != lan_nw.id
    )
    wan_nw = docker.network.list(filters={"id": wan_nw_id})[0]
    wan_subnet = wan_nw.ipam.config[0]["Subnet"]

    router_ip = next(
        v.ipv4_address for k, v in lan_nw.containers.items() if k == router.id
    ).split("/")[0]

    if args.name is None:
        args.name = f"bns-node-{nonce()}"

    if args.key is None:
        args.key = "".join([uuid.uuid4().hex + uuid.uuid4().hex])

    if ":" not in args.stun:
        args.stun = f"{args.stun}:3478"
    if not args.stun.startswith("stun://"):
        args.stun = f"stun://{args.stun}"

    cmd = ["bns-node", "run", "-b", "0.0.0.0:50000"]
    volumes = []
    if args.debug:
        cmd = ["cargo", "run", "--", "run", "-b", "0.0.0.0:50000"]
        # cannot use readonly mode, cargo will manipulate files
        volumes = [(base_dir / "docker/bns-node", "/src/bns-node")]
        args.node_image = "bnsnet/node-builder"
        args.name = f"{args.name}-debug"

    node = docker.container.run(
        args.node_image,
        cmd,
        name=args.name,
        detach=True,
        cap_add=["NET_ADMIN"],
        networks=[lan_nw],
        labels={"operator": "nind"},
        envs={"ICE_SERVERS": args.stun, "ETH_KEY": args.key, "RUST_BACKTRACE": "1"},
        volumes=volumes,
    )
    node.reload()
    node_ip = node.network_settings.networks[lan_nw.name].ip_address

    logger.info(f"Node Container ID {node.id}")
    logger.info(f"(lan {lan_nw.name}) IP Address {node_ip}")
    logger.info("Add route...")

    node.execute(["ip", "route", "add", wan_subnet, "via", router_ip, "dev", "eth0"])

    print(f"{lan_nw.name} {node.name}")


def clean():
    for c in docker.container.list(all=True, filters={"label": "operator=nind"}):
        c.remove(force=True)
    docker.network.prune(filters={"label": "operator=nind"})


def main():
    args = parse_args()
    init_logger(logging.DEBUG if args.verbose else logging.INFO)

    if args.cmd == "build_image":
        build_image(args)
    elif args.cmd == "create_nat":
        create_nat(args)
    elif args.cmd == "create_node":
        create_node(args)
    elif args.cmd == "clean":
        clean()


if __name__ == "__main__":
    main()

# command line interface

import asyncclick as click
from distkv.util import yprint, attrdict, NotGiven, P, Path, as_service, data_get
from distkv.command import node_attr

import logging

logger = logging.getLogger(__name__)


@main.group(short_help="Manage 1wire devices.")  # pylint: disable=undefined-variable
async def cli():
    """
    List Onewire devices, modify device handling …
    """
    pass


@cli.command("list")
@click.option("-d", "--device", help="Device to access.")
@click.option("-f", "--family", help="Device family to modify.")
@click.pass_obj
async def list_(obj, device, family):
    """Emit the current state as a YAML file.
    """
    if device is not None and family is not None:
        raise click.UsageError("Family and device code can't both be used")
    if family:
        f = int(family, 16)
        path = Path(f)

        def pm(p):
            if len(p) < 1:
                return path
            return Path("%02x.%12x" % (f, p[0]), *p[1:])

    elif device:
        f, d = device.split(".", 2)[0:2]
        path = Path(int(f, 16), int(d, 16))

        def pm(p):
            return Path(device) + p

    else:
        path = Path()

        def pm(p):
            if len(p) == 0:
                return p
            elif not isinstance(p[0], int):
                return None
            elif len(p) == 1:
                return Path("%02x" % p[0])
            else:
                return Path("%02x.%12x" % (p[0], p[1])) + p[2:]

    await data_get(obj, obj.cfg.owfs.prefix + path, as_dict="_", path_mangle=pm)


@cli.command("attr", help="Mirror a device attribute to/from DistKV")
@click.option("-d", "--device", help="Device to access.")
@click.option("-f", "--family", help="Device family to modify.")
@click.option("-i", "--interval", type=float, help="read value every N seconds")
@click.option("-w", "--write", is_flag=True, help="Write to the device")
@click.option("-a", "--attr", "attr_", help="The node's attribute to use", default=":")
@click.argument("attr", nargs=1)
@click.argument("path", nargs=1)
@click.pass_obj
async def attr__(obj, device, family, write, attr, interval, path, attr_):
    """Show/add/modify an entry to repeatedly read an 1wire device's attribute.

    You can only set an interval, not a path, on family codes.
    A path of '-' deletes the entry.
    If you set neither interval nor path, reports the current
    values.
    """
    path = P(path)
    if (device is not None) + (family is not None) != 1:
        raise click.UsageError("Either family or device code must be given")
    if interval and write:
        raise click.UsageError("Writing isn't polled")

    remove = False
    if len(path) == 1 and path[0] == "-":
        path = ()
        remove = True

    if family:
        if path:
            raise click.UsageError("You cannot set a per-family path")
        fd = (int(family, 16),)
    else:
        f, d = device.split(".", 2)[0:2]
        fd = (int(f, 16), int(d, 16))

    attr = P(attr)
    attr_ = P(attr_)
    if remove:
        res = await obj.client.delete(obj.cfg.owfs.prefix + fd + attr)
    else:
        val = dict()
        if path:
            val["src" if write else "dest"] = path
        if interval:
            val["interval"] = interval
        if len(attr_):
            val["src_attr" if write else "dest_attr"] = attr_

        res = await obj.client.set(obj.cfg.owfs.prefix + fd + attr, value=val)

    if res is not None and obj.meta:
        yprint(res, stream=obj.stdout)


@cli.command("set")
@click.option("-d", "--device", help="Device to modify.")
@click.option("-f", "--family", help="Device family to modify.")
@click.option("-v", "--value", help="The attribute to set or delete")
@click.option("-e", "--eval", "eval_", is_flag=True, help="Whether to eval the value")
@click.option("-s", "--split", is_flag=True, help="Split the value into words")
@click.option("-a", "--attr", "attr_", help="The attribute to modify")
@click.argument("name", nargs=1, default=":")
@click.pass_obj
async def set_(obj, device, family, value, eval_, name, split, attr_):
    """Set or delete some random attribute.

    For deletion, use '-ev-'.
    """
    name = P(name)
    if not attr_:
        raise click.UsageError("You need to name the attribute")
    attr_ = P(attr_)
    if (device is not None) + (family is not None) != 1:
        raise click.UsageError("Either family or device code must be given")
    if not len(name):
        raise click.UsageError("You need to name the attribute")
    if eval_ and split:
        raise click.UsageError("Split and eval can't be used together")

    if family:
        fd = (int(family, 16),)
        if len(name):
            raise click.UsageError("You can't use a subpath here.")
    else:
        f, d = device.split(".", 2)[0:2]
        fd = (int(f, 16), int(d, 16))

    if eval_ and value == "-":
        value = NotGiven

    res = await node_attr(
        obj, obj.cfg.owfs.prefix + fd + name, attr_, value=value, eval_=eval_, split_=split
    )
    if res and obj.meta:
        yprint(res, stream=obj.stdout)


@cli.command("server")
@click.option("-h", "--host", help="Host name of this server.")
@click.option("-p", "--port", help="Port of this server.")
@click.option("-d", "--delete", is_flag=True, help="Delete this server.")
@click.argument("name", nargs=-1)
@click.pass_obj
async def server_(obj, name, host, port, delete):
    """
    Configure a server.

    No arguments: list them.
    """
    if not name:
        if host or port or delete:
            raise click.UsageError("Use a server name to set parameters")
        async for r in obj.client.get_tree(
            obj.cfg.owfs.prefix | "server", min_depth=1, max_depth=1
        ):
            print(r.path[-1], file=obj.stdout)
        return
    elif len(name) > 1:
        raise click.UsageError("Only one server allowed")
    name = name[0]
    if host or port:
        if delete:
            raise click.UsageError("You can't delete and set at the same time.")
        value = attrdict()
        if host:
            value.host = host
        if port:
            if port == "-":
                value.port = NotGiven
            else:
                value.port = int(port)
    elif delete:
        res = await obj.client.delete_tree(obj.cfg.owfs.prefix | "server" | name, nchain=obj.meta)
        if obj.meta:
            yprint(res, stream=obj.stdout)
        return
    else:
        value = None
    res = await node_attr(
        obj, obj.cfg.owfs.prefix | "server" | name, P("server"), value, eval_=False
    )
    if res and obj.meta:
        yprint(res, stream=obj.stdout)


@cli.command()
@click.pass_obj
@click.argument("server", nargs=-1)
async def monitor(obj, server):
    """Stand-alone task to monitor one or more OWFS servers.
    """
    from distkv_ext.owfs.task import task

    async with as_service(obj) as srv:
        await task(obj.client, obj.cfg, server, srv)

"""
DistKV client data model for 1wire
"""
import anyio

from distkv.obj import ClientEntry, ClientRoot, AttrClientEntry
from distkv.util import combine_dict, attrdict
from distkv.errors import ErrorRoot
from distkv.exceptions import ClientChainError
from collections.abc import Mapping

import logging

logger = logging.getLogger(__name__)


class OWFSattr(ClientEntry):
    watch_src = None
    watch_src_attr = None
    watch_src_scope = None
    watch_dest = None
    watch_dest_attr = None
    watch_dest_interval = None
    watch_dest_value = None
    watch_dest_chain = None

    @classmethod
    def child_type(cls, name):
        return cls

    @property
    def node(self):
        return self.parent.node

    @property
    def attr(self):
        return self.subpath[2:]  # without the device code

    async def set_value(self, val):  # pylint: disable=arguments-differ
        """
        Some attribute has been updated.
        """
        await super().set_value(val)
        await self._sync(False)

    async def sync(self, force: bool):
        for k in self:
            await k.sync(force)
        await self._sync(force)

    async def _sync(self, force: bool):
        val = combine_dict(self.value_or({}, Mapping), self.node.val)
        dev = self.node.dev
        if dev is None or dev.bus is None:
            return

        # write to OWFS
        src = val.get("src", None)
        src_attr = val.get("src_attr", ())
        if force or src != self.watch_src:
            if self.watch_src_scope is not None:
                await self.watch_src_scope.cancel()
            self.watch_src = src
            self.watch_src_attr = src_attr
            if src is not None:
                evt = anyio.create_event()
                await self.root._tg.spawn(self._watch_src, evt)
                await evt.wait()
            else:
                await self.root.err.record_working(
                    "owfs", self.subpath + ("write",), comment="dropped"
                )

        # poll OWFS
        intv = val.get("interval", 0)
        dest = val.get("dest")
        dest_attr = val.get("dest_attr")
        if force or dest != self.watch_dest or intv != self.watch_dest_interval:
            await dev.set_polling_interval(self.attr, 0)
            if intv > 0 and dest is not None:
                self.watch_dest_interval = intv
            self.watch_dest = dest
            self.watch_dest_attr = dest_attr
            if dest is not None and intv is not None:
                await dev.set_polling_interval(self.attr, intv)
            else:
                await self.root.err.record_working(
                    "owfs", self.subpath + ("read",), comment="dropped"
                )
        self.watch_dest_chain = None

    async def dest_value(self, val):
        """
        Called by the task to update a polled value
        """
        try:
            if self.watch_dest_attr:
                retried = False
                while True:
                    if retried or self.watch_dest_chain is None:
                        res = await self.client.get(self.watch_dest, nchain=3)
                        self.watch_dest_value = res.value
                        self.watch_dest_chain = res.chain if "value" in res else None
                        retried = True
                    nval = self.watch_dest_value
                    if not isinstance(nval, attrdict):
                        nval = attrdict()
                    nval = nval._update(self.watch_dest_attr, val)
                    try:
                        await self.client.set(self.watch_dest, nval, idem=True)
                    except ClientChainError:
                        if retried:
                            raise
                        retried = True
                    break
            else:
                await self.client.set(self.watch_dest, val, idem=True)

        except Exception as exc:
            await self.root.err.record_error("owfs", self.subpath + ("read",), exc=exc)
        else:
            await self.root.err.record_working("owfs", self.subpath + ("read",))

    async def _watch_src(self, evt):
        """
        Task that monitors one entry and writes its value to the 1wire
        device.
        """
        async with anyio.open_cancel_scope() as sc:
            try:
                async with self.client.watch(
                    self.watch_src, min_depth=0, max_depth=0, fetch=True
                ) as wp:
                    if self.watch_src_scope is not None:
                        await self.watch_src_scope.cancel()
                    self.watch_src_scope = sc
                    await evt.set()

                    async for msg in wp:
                        logger.debug("Process %r", msg)
                        if "path" not in msg:
                            continue
                        try:
                            k = "value"
                            val = msg.value
                            for k in self.watch_src_attr:
                                val = val[k]
                        except (KeyError, AttributeError):
                            await self.root.err.record_error(
                                "owfs",
                                self.subpath + ("write",),
                                comment="Attribute missing",
                                data={"key": k, "attr": self.watch_src_attr, "msg": msg},
                            )
                            return
                        else:
                            dev = self.node.dev
                            if dev is None:
                                await self.root.err.record_error(
                                    "owfs", self.subpath + ("write",), comment="device missing"
                                )
                                return
                            await dev.set(*self.attr, value=val)
                            await self.root.err.record_working(
                                "owfs", self.subpath + ("write",), comment="write OK"
                            )

            except Exception as exc:
                await self.root.err.record_error("owfs", self.subpath + ("write",), exc=exc)
            finally:
                if self.watch_src_scope is sc:
                    self.watch_src_scope = None


class OWFSnode(ClientEntry):
    dev = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.val = {}

    @classmethod
    def child_type(cls, name):
        return OWFSattr

    @property
    def node(self):
        return self

    @property
    def family(self):
        return self._path[-2]

    async def sync(self, force: bool = False):
        for k in self:
            await k.sync(force)

    async def with_device(self, dev):
        """
        Called by the OWFS monitor, noting that the device is now visible
        on a bus (or not, if ``None``).
        """
        self.dev = dev
        await self._update_value()
        await self.sync(True)

    async def set_value(self, val):  # pylint: disable=arguments-differ
        """
        Some attribute has been updated.
        """
        await super().set_value(val)
        await self._update_value()
        await self.sync(False)

    async def _update_value(self):
        """
        Synpollers, watchers and attributes.
        """
        dev = self.dev
        if dev is None or dev.bus is None:
            return

        self.val = combine_dict(self.value_or({}, Mapping), self.parent.value_or({}, Mapping))


class OWFSfamily(ClientEntry):
    cls = OWFSnode

    @classmethod
    def child_type(cls, name):
        if not isinstance(name, int):
            return ClientEntry
        if name <= 0 or name > 16 ** 12:
            return ClientEntry
        return cls.cls

    async def set_value(self, val):  # pylint: disable=arguments-differ
        await super().set_value(val)
        for c in self:
            await c._update_value()


class ServerEntry(AttrClientEntry):
    ATTRS = ("server",)

    @classmethod
    def child_type(cls, name):
        return ClientEntry


class ServerRoot(ClientEntry):
    @classmethod
    def child_type(cls, name):
        return ServerEntry


class OWFSroot(ClientRoot):
    cls = {}
    reg = {}
    CFG = "owfs"
    err = None

    async def run_starting(self):
        if self.err is None:
            self.err = await ErrorRoot.as_handler(self.client)
        await super().run_starting()

    @property
    def server(self):
        return self["server"]

    @classmethod
    def register(cls, typ):
        def acc(kls):
            cls.reg[typ] = kls
            return kls

        return acc

    @classmethod
    def child_type(kls, name):
        if not isinstance(name, int):
            return ServerRoot
        if name < 0 or name > 255:
            return ClientEntry
        try:
            return kls.cls[name]
        except KeyError:

            class FamilyX(OWFSfamily):
                cls = kls.reg.get(name, OWFSnode)

            FamilyX.__name__ = "OWFSfamily_%02X" % (name,)
            kls.cls[name] = FamilyX
            return FamilyX


@OWFSroot.register(0x10)
class TempNode(OWFSnode):
    CFG = {"temperature": 30}

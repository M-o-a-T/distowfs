#!/usr/bin/python3

import anyio
from distkv.client import open_client
from distkv.util import P

async def mod_owfs():
    async with open_client() as c:
        async for r in c.get_tree(P(":.distkv.onewire"),min_depth=2,max_depth=2,nchain=2):
            try:
                at = r.value.pop("attr")
            except KeyError:
                continue
            await c.set(P(":.distkv.onewire")+r.path,value=r.value,chain=r.chain)
#           for k,v in at.items():
#               print(r.path+P(k.replace('/',':')),v)
#               await c.set(P(":.distkv.onewire")+r.path+P(k.replace('/',':')),value=v)

anyio.run(mod_owfs)


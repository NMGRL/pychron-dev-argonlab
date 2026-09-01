import base64
import json 
import os
import struct 


def load_dat(repo_dir, uuid):
    """
    repo_dir is path to repo root (eg) .../hornblende_snapshot/repos/Felix_blank260
    uuid: full analysis uuid
    returns {"signals": {detector: (xs,ys)}, "sniffs": {...}, "baselines": {...}}
    """
    p = os.path.join(repo_dir, uuid[:2], ".data", "{}.dat.json".format(uuid[2:]))
    with open(p) as rfile:
        obj = json.load(rfile)

    fmt = obj.get("format", ">ff")
    out = {"signals": {}, "sniffs": {}, "baselines": {}}
    for kind in out:
        for e in obj.get(kind, []):
            blob = base64.b64decode(e["blob"])
            pts = [struct.unpack(fmt, blob[i : i + 8]) for i in range(0,len(blob), 8)]
            xs, ys = zip(*pts) if pts else ((),())
            out[kind][e["detector"]] = (xs, ys)
    return out 

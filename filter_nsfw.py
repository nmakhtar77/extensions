#!/usr/bin/env python3
"""Retire les extensions +18 de l'index Keiyoushi (format v2).

À lancer dans le dossier qui contient index.json, index.pb et repo.json
(la branche "repo" de keiyoushi/extensions).

- index.json : liste lisible, sert à savoir quelle extension est +18
- index.pb   : le fichier que Mihon lit vraiment (protobuf, souvent en gzip)
- repo.json  : indique à Mihon où trouver index.pb

Le script filtre index.json ET index.pb, puis fait pointer repo.json
vers le dépôt filtré. Si quelque chose ne colle pas, il s'arrête sans
rien écrire (code de sortie 1), pour ne jamais publier un fichier cassé.
"""
import gzip
import json
import sys

# Marqueurs à retirer. Pour être plus strict (sites mixtes comme Comick,
# DeviantArt, GlobalComix...), ajouter "CONTENT_WARNING_MIXED".
REMOVE = {"CONTENT_WARNING_NSFW", "CONTENT_WARNING_MIXED"}

# Adresse du dépôt filtré (là où index.pb sera publié).
BASE = "https://raw.githubusercontent.com/nmakhtar77/extensions/repo"


# ---------- lecture / écriture protobuf sans schéma ----------

def read_varint(b, i):
    n = shift = 0
    while True:
        if i >= len(b):
            raise ValueError("varint tronqué")
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7
        if shift > 70:
            raise ValueError("varint trop long")


def write_varint(n):
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        if n:
            out.append(c | 0x80)
        else:
            out.append(c)
            return bytes(out)


def parse(b):
    """Découpe un message protobuf en [(numéro, type, valeur), ...]."""
    fields, i = [], 0
    while i < len(b):
        key, i = read_varint(b, i)
        num, wt = key >> 3, key & 7
        if num == 0:
            raise ValueError("numéro de champ invalide")
        if wt == 0:
            v, i = read_varint(b, i)
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 2:
            ln, i = read_varint(b, i)
            v, i = b[i:i + ln], i + ln
            if len(v) != ln:
                raise ValueError("champ tronqué")
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError("type de champ non géré")
        fields.append((num, wt, v))
    return fields


def try_parse(b):
    try:
        return parse(b)
    except Exception:
        return None


def encode(fields):
    out = bytearray()
    for num, wt, v in fields:
        out += write_varint((num << 3) | wt)
        if wt == 0:
            out += write_varint(v)
        elif wt == 2:
            out += write_varint(len(v)) + v
        else:
            out += v
    return bytes(out)


# ---------- filtrage ----------

def filter_pb(raw, warnings):
    """warnings : {packageName: contentWarning} tiré de index.json."""
    gz = raw[:2] == b"\x1f\x8b"
    body = gzip.decompress(raw) if gz else raw
    top = parse(body)

    if encode(top) != body:
        print("Attention : ré-encodage non identique à l'original (sans gravité en général).")

    def entry_pkg(payload):
        sub = try_parse(payload)
        if not sub:
            return None
        for _, wt, v in sub:
            if wt == 2:
                try:
                    s = v.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if s in warnings:
                    return s
        return None

    # Trouver le sous-message qui contient la liste des extensions.
    best = None
    for idx, (_, wt, v) in enumerate(top):
        if wt != 2:
            continue
        sub = try_parse(v)
        if not sub:
            continue
        n = sum(1 for _, w2, v2 in sub if w2 == 2 and entry_pkg(v2))
        if n and (best is None or n > best[0]):
            best = (n, idx, sub)
    if best is None:
        raise ValueError("liste d'extensions introuvable dans index.pb")

    n_found, idx, sub = best
    if n_found != len(warnings):
        raise ValueError(f"index.pb contient {n_found} extensions, index.json en contient {len(warnings)}")

    kept, removed = [], 0
    for item in sub:
        _, wt, v = item
        if wt == 2:
            pkg = entry_pkg(v)
            if pkg and warnings[pkg] in REMOVE:
                removed += 1
                continue
        kept.append(item)

    top[idx] = (top[idx][0], 2, encode(kept))
    out = encode(top)
    return (gzip.compress(out, mtime=0) if gz else out), removed


def main():
    with open("index.json", encoding="utf-8") as f:
        data = json.load(f)

    exts = data["extensionList"]["extensions"]
    warnings = {e["packageName"]: e.get("contentWarning", "") for e in exts}
    expected = sum(1 for w in warnings.values() if w in REMOVE)
    if expected == 0:
        sys.exit("Aucune extension à retirer : format inattendu, arrêt.")

    with open("index.pb", "rb") as f:
        new_pb, removed = filter_pb(f.read(), warnings)
    if removed != expected:
        sys.exit(f"Incohérence : {removed} retirées dans index.pb, {expected} attendues d'après index.json.")

    data["extensionList"]["extensions"] = [e for e in exts if e.get("contentWarning", "") not in REMOVE]
    with open("index.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open("index.pb", "wb") as f:
        f.write(new_pb)

    with open("repo.json", encoding="utf-8") as f:
        repo = json.load(f)
    repo["index_v2"] = BASE + "/index.pb"
    repo.setdefault("meta", {})["name"] = repo["meta"].get("name", "Keiyoushi") + " (sans +18)"
    with open("repo.json", "w", encoding="utf-8") as f:
        json.dump(repo, f, ensure_ascii=False, indent=2)

    print(f"{len(exts)} extensions -> {len(exts) - removed} conservées, {removed} retirées")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Ricalcola il "filo rosso" (il percorso segnato in rosso sulla mappa)
e lo salva dentro dati.js.

Il percorso NON e' inventato dal computer: segue l'itinerario deciso
da chi conosce il paese, scritto qui sotto nella tabella ITINERARIO,
via per via. Il programma si limita a disegnarlo sulle strade vere.

QUANDO USARLO
  - dopo aver spostato uno o piu' punti (modalita' ?modifica);
  - se si vuole cambiare il giro: basta modificare ITINERARIO qui
    sotto, elencando le vie da seguire tra un pannello e l'altro.

COME USARLO
  doppio click su questo file, oppure da terminale:
      python aggiorna_percorso.py
  Serve Python e la connessione a internet (scarica le strade da
  OpenStreetMap; se i server non rispondono usa la copia salvata in
  rete_osm_cache.json). La mappa pubblicata NON dipende da tutto
  questo: il percorso finito viene salvato dentro dati.js.
"""
import json, math, re, sys, heapq, urllib.request, urllib.parse, pathlib

CARTELLA = pathlib.Path(__file__).resolve().parent.parent
DATI = CARTELLA / "dati.js"

# ---------------------------------------------------------------
# L'ITINERARIO, tratto per tratto:
#   (da quale pannello, a quale pannello, [vie da seguire])
# Le vie elencate vengono preferite; i brevi raccordi tra una via e
# l'altra il programma li trova da solo.
# ---------------------------------------------------------------
ITINERARIO = [
    (1,  2,  ["Via Val Cosa", "Strada Provinciale 22 della Val Cosa"]),
    (2,  3,  ["Via Zancan"]),
    (3,  4,  ["Piazza Venti Settembre", "Via Praforte"]),
    (4,  5,  ["Via Praforte"]),
    (5,  6,  ["Via Praforte", "Via Riosecco"]),
    (6,  7,  ["Via Riosecco", "Via Villa"]),
    (7,  8,  ["Via Gondei", "Via Roma", "Via Stazione", "Via Dante Alighieri"]),
    (8,  9,  ["Via Dante Alighieri", "Via Fornasatta", "Via Fontana"]),
    (9,  10, ["Via Fontana", "Via Giuseppe Mazzini"]),
    (10, 11, ["Via Giuseppe Mazzini", "Via Val Cosa", "Via Giuseppe Garibaldi"]),
    (11, 1,  ["Via Giuseppe Garibaldi", "Via Molevana", "Via Val Cosa"]),
]

# Quanto vengono "preferite" le vie dell'itinerario: 0.25 significa
# che percorrerle costa un quarto rispetto alle altre strade, cosi'
# il percorso le segue anche se non sono la scorciatoia assoluta.
SCONTO_VIE_ITINERARIO = 0.25
VEL_KMH = 4.5  # andatura a piedi, per la stima della durata

TIPI_STRADA = ["path", "footway", "track", "steps", "pedestrian", "cycleway",
               "bridleway", "residential", "living_street", "unclassified",
               "service", "tertiary", "secondary", "primary"]


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def leggi_punti():
    src = DATI.read_text(encoding="utf-8")
    trovati = re.findall(r"num:\s*(\d+),.*?lat:\s*([\d.]+),\s*lon:\s*([\d.]+)", src, re.S)
    return src, {int(n): (float(la), float(lo)) for n, la, lo in trovati}


def scarica_rete(punti):
    lats = [p[0] for p in punti.values()]
    lons = [p[1] for p in punti.values()]
    s, n = min(lats) - 0.012, max(lats) + 0.012
    w, e = min(lons) - 0.017, max(lons) + 0.017
    query = ('[out:json][timeout:90][bbox:%f,%f,%f,%f];way["highway"~"^(%s)$"];out geom;'
             % (s, w, n, e, "|".join(TIPI_STRADA)))
    servers = ["https://overpass-api.de/api/interpreter",
               "https://overpass.private.coffee/api/interpreter",
               "https://overpass.kumi.systems/api/interpreter"]
    cache = pathlib.Path(__file__).resolve().parent / "rete_osm_cache.json"
    for srv in servers:
        try:
            req = urllib.request.Request(
                srv, data=("data=" + urllib.parse.quote(query)).encode(),
                headers={"User-Agent": "MappaTerremotoTravesio/1.0 (progetto culturale)"})
            d = json.load(urllib.request.urlopen(req, timeout=120))
            if d.get("elements"):
                try:
                    cache.write_text(json.dumps(d["elements"]), encoding="utf-8")
                except OSError:
                    pass
                return d["elements"]
        except Exception as ex:
            print("  (server %s non disponibile: %s)" % (srv.split('/')[2], ex))
    if cache.exists():
        print("ATTENZIONE: server non raggiungibili, uso la copia locale salvata")
        print("l'ultima volta (le strade cambiano di rado, va bene lo stesso).")
        return json.loads(cache.read_text(encoding="utf-8"))
    sys.exit("ERRORE: nessun server OpenStreetMap raggiungibile, riprovare piu' tardi.")


def costruisci_grafo(vie):
    """nodi: id -> (lat, lon);  archi: id -> [(vicino, metri, nome via)]"""
    nodi, archi = {}, {}
    for w in vie:
        t = w.get("tags", {})
        if t.get("area") == "yes" or t.get("foot") == "no":
            continue
        acc = t.get("access")
        if acc in ("private", "no") and t.get("foot") not in ("yes", "designated", "permissive"):
            continue
        nome = t.get("name", "")
        ids, geom = w.get("nodes", []), w.get("geometry", [])
        if len(ids) != len(geom):
            continue
        for i in range(len(ids)):
            nodi[ids[i]] = (geom[i]["lat"], geom[i]["lon"])
        for i in range(len(ids) - 1):
            a, b = ids[i], ids[i + 1]
            m = haversine(nodi[a], nodi[b])
            archi.setdefault(a, []).append((b, m, nome))
            archi.setdefault(b, []).append((a, m, nome))
    return nodi, archi


def nodo_vicino(nodi, lat, lon):
    return min(nodi, key=lambda n: (nodi[n][0] - lat) ** 2 + ((nodi[n][1] - lon) * 0.69) ** 2)


def cammino(nodi, archi, da, a, vie_preferite):
    """cammino piu' breve, con forte preferenza per le vie dell'itinerario"""
    preferite = set(vie_preferite)
    dist, prima, coda, visti = {da: 0.0}, {}, [(0.0, da)], set()
    while coda:
        d, n = heapq.heappop(coda)
        if n in visti:
            continue
        visti.add(n)
        if n == a:
            break
        for (v, m, nome) in archi.get(n, []):
            costo = m * (SCONTO_VIE_ITINERARIO if nome in preferite else 1.0)
            if d + costo < dist.get(v, 1e18):
                dist[v] = d + costo
                prima[v] = n
                heapq.heappush(coda, (d + costo, v))
    if a not in prima and a != da:
        return None
    seq = [a]
    while seq[-1] != da:
        seq.append(prima[seq[-1]])
    seq.reverse()
    return seq


def vie_percorse(archi, seq):
    """elenco ordinato delle vie attraversate, senza ripetizioni di fila"""
    elenco = []
    for i in range(len(seq) - 1):
        nome = next((nm for (v, m, nm) in archi.get(seq[i], []) if v == seq[i + 1]), "")
        if nome and (not elenco or elenco[-1] != nome):
            elenco.append(nome)
    return elenco


def main():
    src, punti = leggi_punti()
    if not punti:
        sys.exit("ERRORE: non trovo i punti in dati.js")
    print("Pannelli trovati:", ", ".join(str(n) for n in sorted(punti)))
    print("Scarico le strade da OpenStreetMap...")
    vie = scarica_rete(punti)
    nodi, archi = costruisci_grafo(vie)
    print("Rete stradale: %d incroci, %d vie\n" % (len(nodi), len(vie)))

    linea_ids, metri_tot = [], 0.0
    for (da, a, strade) in ITINERARIO:
        if da not in punti or a not in punti:
            sys.exit("ERRORE: nell'itinerario compare un pannello inesistente (%d o %d)" % (da, a))
        n_da = nodo_vicino(nodi, *punti[da])
        n_a = nodo_vicino(nodi, *punti[a])
        seq = cammino(nodi, archi, n_da, n_a, strade)
        if seq is None:
            sys.exit("ERRORE: non trovo un percorso dal pannello %d al %d." % (da, a))
        metri = sum(haversine(nodi[seq[j]], nodi[seq[j + 1]]) for j in range(len(seq) - 1))
        metri_tot += metri
        print("  %2d -> %-2d  %5d m   %s" % (da, a, round(metri), " > ".join(vie_percorse(archi, seq))))
        linea_ids.extend(seq if not linea_ids else seq[1:])

    km = round(metri_tot / 1000, 1)
    minuti = int(round(metri_tot / 1000 / VEL_KMH * 60))
    print("\nTOTALE: %s km, circa %d minuti a piedi" % (km, minuti))

    linea = ",".join("[%s,%s]" % (round(nodi[n][0], 5), round(nodi[n][1], 5)) for n in linea_ids)
    nuovo = "window.PERCORSO = {\n  km: %s,\n  min: %s,\n  linea: [%s]\n};" % (km, minuti, linea)
    aggiornato, cnt = re.subn(r"window\.PERCORSO\s*=\s*\{.*?\};", nuovo, src, count=1, flags=re.S)
    if cnt != 1:
        sys.exit("ERRORE: non trovo il blocco window.PERCORSO in dati.js")
    DATI.write_text(aggiornato, encoding="utf-8")
    print("dati.js aggiornato. Ricarica la mappa per vedere il nuovo percorso.")


if __name__ == "__main__":
    main()
    try:
        input("\nPremi Invio per chiudere...")
    except EOFError:
        pass

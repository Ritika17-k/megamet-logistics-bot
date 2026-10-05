import places

DEFAULT_ORIGIN = "mumbai"

KNOWN_DISTANCES = {
    key: places.road_distance_km(DEFAULT_ORIGIN, key)
    for key in places.CITIES
}
KNOWN_DISTANCES.update({
    state: places.road_distance_km(DEFAULT_ORIGIN, target)
    for state, target in places.STATE_CENTERS.items()
})

def resolve_distance(place: str, origin: str = DEFAULT_ORIGIN):
   
    if not place:
        return None
    dest_key = places.resolve_place(place)
    if dest_key is None:
        return None
    origin_key = places.resolve_place(origin) or DEFAULT_ORIGIN
    return places.road_distance_km(origin_key, dest_key)

def known_place_names():
    return list(places.CITIES.keys())

def suggest_region_from_coverage(coverage: str, threshold_km, origin: str = DEFAULT_ORIGIN):
    if not isinstance(coverage, str) or not coverage.strip():
        return None

    origin_key = places.resolve_place(origin) or DEFAULT_ORIGIN
    distances = []
    for raw_place in coverage.split(","):
        raw_place = raw_place.strip()
        if not raw_place:
            continue
        dest_key = places.resolve_place(raw_place)
        if dest_key is None:
            continue
        dist = places.road_distance_km(origin_key, dest_key)
        if dist is not None:
            distances.append(dist)

    if not distances:
        return None
    return "Regional" if max(distances) <= threshold_km else "Pan-India"
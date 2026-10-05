import math
import re
from difflib import get_close_matches
 
CITIES = {
    "mumbai": (19.0760, 72.8777, "Mumbai", "Maharashtra"),
    "pune": (18.5204, 73.8567, "Pune", "Maharashtra"),
    "bhor": (18.1500, 73.8460, "Bhor", "Maharashtra"),
    "khopoli": (18.7890, 73.3450, "Khopoli", "Maharashtra"),
    "lonavala": (18.7546, 73.4062, "Lonavala", "Maharashtra"),
    "nagpur": (21.1458, 79.0882, "Nagpur", "Maharashtra"),
    "nashik": (19.9975, 73.7898, "Nashik", "Maharashtra"),
    "thane": (19.2183, 72.9781, "Thane", "Maharashtra"),
    "aurangabad": (19.8762, 75.3433, "Chhatrapati Sambhajinagar (Aurangabad)", "Maharashtra"),
    "kolhapur": (16.7050, 74.2433, "Kolhapur", "Maharashtra"),
    "solapur": (17.6599, 75.9064, "Solapur", "Maharashtra"),
    "wada": (19.9333, 73.1167, "Wada", "Maharashtra"),
    "navi mumbai": (19.0330, 73.0297, "Navi Mumbai", "Maharashtra"),
    "nagothane": (18.5333, 73.1167, "Nagothane", "Maharashtra"),
    "ahmedabad": (23.0225, 72.5714, "Ahmedabad", "Gujarat"),
    "nadiad": (22.6916, 72.8634, "Nadiad", "Gujarat"),
    "surat": (21.1702, 72.8311, "Surat", "Gujarat"),
    "vadodara": (22.3072, 73.1812, "Vadodara", "Gujarat"),
    "rajkot": (22.3039, 70.8022, "Rajkot", "Gujarat"),
    "bhavnagar": (21.7645, 72.1519, "Bhavnagar", "Gujarat"),
    "gandhinagar": (23.2156, 72.6369, "Gandhinagar", "Gujarat"),
    "kandla": (23.0333, 70.2167, "Kandla", "Gujarat"),
    "jamnagar": (22.4707, 70.0577, "Jamnagar", "Gujarat"),
    "bengaluru": (12.9716, 77.5946, "Bengaluru", "Karnataka"),
    "mysuru": (12.2958, 76.6394, "Mysuru", "Karnataka"),
    "mangaluru": (12.9141, 74.8560, "Mangaluru", "Karnataka"),
    "hubballi": (15.3647, 75.1240, "Hubballi", "Karnataka"),
    "belagavi": (15.8497, 74.4977, "Belagavi", "Karnataka"),
    "hyderabad": (17.3850, 78.4867, "Hyderabad", "Telangana"),
    "warangal": (17.9689, 79.5941, "Warangal", "Telangana"),
    "nizamabad": (18.6725, 78.0941, "Nizamabad", "Telangana"),
    "visakhapatnam": (17.6868, 83.2185, "Visakhapatnam", "Andhra Pradesh"),
    "vijayawada": (16.5062, 80.6480, "Vijayawada", "Andhra Pradesh"),
    "guntur": (16.3067, 80.4365, "Guntur", "Andhra Pradesh"),
    "tirupati": (13.6288, 79.4192, "Tirupati", "Andhra Pradesh"),
    "nellore": (14.4426, 79.9865, "Nellore", "Andhra Pradesh"),
    "kakinada": (16.9891, 82.2475, "Kakinada", "Andhra Pradesh"),
    "chennai": (13.0827, 80.2707, "Chennai", "Tamil Nadu"),
    "coimbatore": (11.0168, 76.9558, "Coimbatore", "Tamil Nadu"),
    "madurai": (9.9252, 78.1198, "Madurai", "Tamil Nadu"),
    "tiruchirappalli": (10.7905, 78.7047, "Tiruchirappalli", "Tamil Nadu"),
    "salem": (11.6643, 78.1460, "Salem", "Tamil Nadu"),
    "kancheepuram": (12.8342, 79.7036, "Kancheepuram", "Tamil Nadu"),
    "tuticorin": (8.7642, 78.1348, "Tuticorin", "Tamil Nadu"),
    "erode": (11.3410, 77.7172, "Erode", "Tamil Nadu"),
    "kochi": (9.9312, 76.2673, "Kochi", "Kerala"),
    "thiruvananthapuram": (8.5241, 76.9366, "Thiruvananthapuram", "Kerala"),
    "kozhikode": (11.2588, 75.7804, "Kozhikode", "Kerala"),
    "thrissur": (10.5276, 76.2144, "Thrissur", "Kerala"),
    "kollam": (8.8932, 76.6141, "Kollam", "Kerala"),
    "kolkata": (22.5726, 88.3639, "Kolkata", "West Bengal"),
    "howrah": (22.5958, 88.2636, "Howrah", "West Bengal"),
    "siliguri": (26.7271, 88.3953, "Siliguri", "West Bengal"),
    "durgapur": (23.5204, 87.3119, "Durgapur", "West Bengal"),
    "asansol": (23.6739, 86.9524, "Asansol", "West Bengal"),
    "haldia": (22.0667, 88.0698, "Haldia", "West Bengal"),
    "delhi": (28.7041, 77.1025, "Delhi", "Delhi"),
    "new delhi": (28.6139, 77.2090, "New Delhi", "Delhi"),
    "gurugram": (28.4595, 77.0266, "Gurugram", "Haryana"),
    "noida": (28.5355, 77.3910, "Noida", "Uttar Pradesh"),
    "faridabad": (28.4089, 77.3178, "Faridabad", "Haryana"),
    "ghaziabad": (28.6692, 77.4538, "Ghaziabad", "Uttar Pradesh"),
    "lucknow": (26.8467, 80.9462, "Lucknow", "Uttar Pradesh"),
    "kanpur": (26.4499, 80.3319, "Kanpur", "Uttar Pradesh"),
    "agra": (27.1767, 78.0081, "Agra", "Uttar Pradesh"),
    "varanasi": (25.3176, 82.9739, "Varanasi", "Uttar Pradesh"),
    "meerut": (28.9845, 77.7064, "Meerut", "Uttar Pradesh"),
    "prayagraj": (25.4358, 81.8463, "Prayagraj", "Uttar Pradesh"),
    "bareilly": (28.3670, 79.4304, "Bareilly", "Uttar Pradesh"),
    "aligarh": (27.8974, 78.0880, "Aligarh", "Uttar Pradesh"),
    "gorakhpur": (26.7606, 83.3732, "Gorakhpur", "Uttar Pradesh"),
    "jaipur": (26.9124, 75.7873, "Jaipur", "Rajasthan"),
    "jodhpur": (26.2389, 73.0243, "Jodhpur", "Rajasthan"),
    "udaipur": (24.5854, 73.7125, "Udaipur", "Rajasthan"),
    "kota": (25.2138, 75.8648, "Kota", "Rajasthan"),
    "ajmer": (26.4499, 74.6399, "Ajmer", "Rajasthan"),
    "bikaner": (28.0229, 73.3119, "Bikaner", "Rajasthan"),
    "bhopal": (23.2599, 77.4126, "Bhopal", "Madhya Pradesh"),
    "indore": (22.7196, 75.8577, "Indore", "Madhya Pradesh"),
    "gwalior": (26.2183, 78.1828, "Gwalior", "Madhya Pradesh"),
    "jabalpur": (23.1815, 79.9864, "Jabalpur", "Madhya Pradesh"),
    "ujjain": (23.1765, 75.7885, "Ujjain", "Madhya Pradesh"),
    "ludhiana": (30.9010, 75.8573, "Ludhiana", "Punjab"),
    "amritsar": (31.6340, 74.8723, "Amritsar", "Punjab"),
    "jalandhar": (31.3260, 75.5762, "Jalandhar", "Punjab"),
    "patiala": (30.3398, 76.3869, "Patiala", "Punjab"),
    "chandigarh": (30.7333, 76.7794, "Chandigarh", "Chandigarh"),
    "panipat": (29.3909, 76.9635, "Panipat", "Haryana"),
    "karnal": (29.6857, 76.9905, "Karnal", "Haryana"),
    "hisar": (29.1492, 75.7217, "Hisar", "Haryana"),
    "rohtak": (28.8955, 76.6066, "Rohtak", "Haryana"),
    "patna": (25.5941, 85.1376, "Patna", "Bihar"),
    "gaya": (24.7955, 84.9994, "Gaya", "Bihar"),
    "bhagalpur": (25.2425, 86.9842, "Bhagalpur", "Bihar"),
    "muzaffarpur": (26.1225, 85.3906, "Muzaffarpur", "Bihar"),
    "ranchi": (23.3441, 85.3096, "Ranchi", "Jharkhand"),
    "jamshedpur": (22.8046, 86.2029, "Jamshedpur", "Jharkhand"),
    "dhanbad": (23.7957, 86.4304, "Dhanbad", "Jharkhand"),
    "bokaro": (23.6693, 86.1511, "Bokaro", "Jharkhand"),
    "bhubaneswar": (20.2961, 85.8245, "Bhubaneswar", "Odisha"),
    "cuttack": (20.4625, 85.8828, "Cuttack", "Odisha"),
    "rourkela": (22.2604, 84.8536, "Rourkela", "Odisha"),
    "paradip": (20.3167, 86.6167, "Paradip", "Odisha"),
    "guwahati": (26.1445, 91.7362, "Guwahati", "Assam"),
    "dibrugarh": (27.4728, 94.9120, "Dibrugarh", "Assam"),
    "silchar": (24.8333, 92.7789, "Silchar", "Assam"),
    "raipur": (21.2514, 81.6296, "Raipur", "Chhattisgarh"),
    "bhilai": (21.1938, 81.3509, "Bhilai", "Chhattisgarh"),
    "bilaspur": (22.0797, 82.1391, "Bilaspur", "Chhattisgarh"),
    "dehradun": (30.3165, 78.0322, "Dehradun", "Uttarakhand"),
    "haridwar": (29.9457, 78.1642, "Haridwar", "Uttarakhand"),
    "nainital": (29.3919, 79.4542, "Nainital", "Uttarakhand"),
    "shimla": (31.1048, 77.1734, "Shimla", "Himachal Pradesh"),
    "manali": (32.2396, 77.1887, "Manali", "Himachal Pradesh"),
    "srinagar": (34.0837, 74.7973, "Srinagar", "Jammu and Kashmir"),
    "jammu": (32.7266, 74.8570, "Jammu", "Jammu and Kashmir"),
    "leh": (34.1526, 77.5771, "Leh", "Ladakh"),
    "panaji": (15.4909, 73.8278, "Panaji", "Goa"),
    "margao": (15.2832, 73.9862, "Margao", "Goa"),
    "agartala": (23.8315, 91.2868, "Agartala", "Tripura"),
    "imphal": (24.8170, 93.9368, "Imphal", "Manipur"),
    "shillong": (25.5788, 91.8933, "Shillong", "Meghalaya"),
    "aizawl": (23.7271, 92.7176, "Aizawl", "Mizoram"),
    "kohima": (25.6751, 94.1086, "Kohima", "Nagaland"),
    "itanagar": (27.0844, 93.6053, "Itanagar", "Arunachal Pradesh"),
    "gangtok": (27.3389, 88.6065, "Gangtok", "Sikkim"),
    "puducherry": (11.9416, 79.8083, "Puducherry", "Puducherry"),
    "port blair": (11.6234, 92.7265, "Port Blair", "Andaman and Nicobar Islands"),
    "daman": (20.3974, 72.8328, "Daman", "Dadra and Nagar Haveli and Daman and Diu"),
}
 
ALIASES = {
    "bombay": "mumbai",
    "bombai": "mumbai",
    "bengalooru": "bengaluru",
    "bangalore": "bengaluru",
    "banglore": "bengaluru",
    "calcutta": "kolkata",
    "madras": "chennai",
    "trivandrum": "thiruvananthapuram",
    "tvm": "thiruvananthapuram",
    "cochin": "kochi",
    "mysore": "mysuru",
    "poona": "pune",
    "vizag": "visakhapatnam",
    "vishakhapatnam": "visakhapatnam",
    "gurgaon": "gurugram",
    "allahabad": "prayagraj",
    "baroda": "vadodara",
    "calicut": "kozhikode",
    "cawnpore": "kanpur",
    "pondicherry": "puducherry",
    "belgaum": "belagavi",
    "hubli": "hubballi",
    "mangalore": "mangaluru",
    "ncr": "delhi",
    "new delhi": "new delhi",
    "b'lore": "bengaluru",
    "hyd": "hyderabad",
    "blr": "bengaluru",
    "bom": "mumbai",
    "del": "delhi",
    "maa": "chennai",
    "ccu": "kolkata",
    "nava sheva": "navi mumbai",
    "nhava sheva": "navi mumbai",
    "jnpt": "navi mumbai",
    "dahisar mori": "mumbai",
    "dahisar": "mumbai",
    "ray road": "mumbai",
    "reay road": "mumbai",
    "malad": "mumbai",
    "goregaon": "mumbai",
    "goregoan": "mumbai",
    "vasai": "mumbai",
    "goregam": "mumbai",
    "navashva": "navi mumbai",
    "navasheva": "navi mumbai",
    "bhiwandi": "thane",
    "kalyan": "thane",
    "dombivli": "thane",
    "taloja": "navi mumbai",
    "panvel": "navi mumbai",
    "kalamboli": "navi mumbai",
    "turbhe": "navi mumbai",
    "uran": "navi mumbai",
    "secunderabad": "hyderabad",
}
 
STATE_CENTERS = {
    "maharashtra": "mumbai",
    "gujarat": "ahmedabad",
    "karnataka": "bengaluru",
    "telangana": "hyderabad",
    "andhra pradesh": "vijayawada",
    "tamil nadu": "chennai",
    "kerala": "kochi",
    "west bengal": "kolkata",
    "delhi": "delhi",
    "uttar pradesh": "lucknow",
    "rajasthan": "jaipur",
    "madhya pradesh": "bhopal",
    "punjab": "ludhiana",
    "haryana": "gurugram",
    "bihar": "patna",
    "jharkhand": "ranchi",
    "odisha": "bhubaneswar",
    "orissa": "bhubaneswar",
    "assam": "guwahati",
    "chhattisgarh": "raipur",
    "uttarakhand": "dehradun",
    "himachal pradesh": "shimla",
    "jammu and kashmir": "srinagar",
    "j&k": "srinagar",
    "ladakh": "leh",
    "goa": "panaji",
    "tripura": "agartala",
    "manipur": "imphal",
    "meghalaya": "shillong",
    "mizoram": "aizawl",
    "nagaland": "kohima",
    "arunachal pradesh": "itanagar",
    "sikkim": "gangtok",
    "puducherry": "puducherry",
    "andaman and nicobar islands": "port blair",
    "andaman": "port blair",
    "chandigarh": "chandigarh",
}
 
ROAD_DISTANCE_FACTOR = 1.30
SAME_CITY_LOCAL_KM = 15
 
_FUZZY_CUTOFF = 0.82
_STOPWORDS = {
    "the", "a", "an", "to", "from", "for", "and", "of", "in", "on", "at",
    "is", "it", "be", "with", "this", "that", "ton", "tons", "tonne",
    "tonnes", "kg", "load", "truck", "need", "want", "please", "hi", "hii",
    "hello", "hey", "se", "tak", "ke", "liye", "chahiye", "pickup",
    "destination", "weight",
}
 
 
def _lookup_table():
    table = {}
    for key in CITIES:
        table[key] = key
    for alias, target in ALIASES.items():
        table[alias] = target
    for state, target in STATE_CENTERS.items():
        table.setdefault(state, target)
    return table
 
 
_LOOKUP = _lookup_table()
_ALL_KEYS_BY_LEN = sorted(_LOOKUP.keys(), key=len, reverse=True)
 
 
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
 
 
def resolve_place(text: str):
    norm = normalize(text)
    if not norm:
        return None
    if norm in _LOOKUP:
        return _LOOKUP[norm]
 
    for name in _ALL_KEYS_BY_LEN:
        if re.search(r"\b" + re.escape(name) + r"\b", norm):
            return _LOOKUP[name]
 
    if norm not in _STOPWORDS and len(norm) >= 4:
        candidates = get_close_matches(norm, _ALL_KEYS_BY_LEN, n=1, cutoff=_FUZZY_CUTOFF)
        if candidates:
            return _LOOKUP[candidates[0]]
    return None
 
 
def find_place_in_text(text: str):
    norm = normalize(text)
    if not norm:
        return None, None
 
    for name in _ALL_KEYS_BY_LEN:
        m = re.search(r"\b" + re.escape(name) + r"\b", norm)
        if m:
            return _LOOKUP[name], m.group(0)
 
    words = [w for w in re.split(r"[^a-z]+", norm) if w]
    windows = list(words)
    windows += [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
    for w in windows:
        if w in _STOPWORDS or len(w) < 4:
            continue
        candidates = get_close_matches(w, _ALL_KEYS_BY_LEN, n=1, cutoff=_FUZZY_CUTOFF)
        if candidates:
            return _LOOKUP[candidates[0]], w
    return None, None
 
 
_KEEP_NAME_HUBS = {"mumbai", "navi mumbai", "thane", "hyderabad"}
LOCAL_AREAS = {a: t for a, t in ALIASES.items()
               if t in _KEEP_NAME_HUBS and (len(a) > 4 or a == "jnpt") and a not in ("bombay", "bombai")}
_AREA_CAPS = {"jnpt": "JNPT"}


def area_mention(text: str, key: str):
    norm = normalize(text)
    if not key or re.search(r"\b" + re.escape(key) + r"\b", norm):
        return None
    hits = [a for a, t in LOCAL_AREAS.items()
            if t == key and re.search(r"\b" + re.escape(a) + r"\b", norm)]
    hits = [a for a in hits if not any(a != b and a in b for b in hits)]
    if len(hits) != 1:
        return None
    return _AREA_CAPS.get(hits[0], hits[0].title())


def place_display(key: str) -> str:
    if key in CITIES:
        return CITIES[key][2]
    return key.title()
 
 
def place_state(key: str):
    if key in CITIES:
        return CITIES[key][3]
    return None
 
 
def known_place_count() -> int:
    return len(CITIES)
 
 
def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))
 
 
def road_distance_km(origin_key: str, destination_key: str):
    if origin_key not in CITIES or destination_key not in CITIES:
        return None
    if origin_key == destination_key:
        return SAME_CITY_LOCAL_KM
    lat1, lon1, _, _ = CITIES[origin_key]
    lat2, lon2, _, _ = CITIES[destination_key]
    straight = _haversine_km(lat1, lon1, lat2, lon2)
    est = straight * ROAD_DISTANCE_FACTOR
    return int(round(est / 5.0) * 5)
 
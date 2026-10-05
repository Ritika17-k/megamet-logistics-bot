import sys

import auction
import transporter_list as tl

if __name__ == "__main__":
    if len(sys.argv) < 3:
        rows = tl.load()
        print(f"{len(rows)} transporters in {tl.LIST_PATH.name}\n")
        for t in rows:
            print(f"{t['sr']:>3} {t['name'][:28]:<28} {t['phone'] or '-':<11} {tl.describe(t)}")
            for p in t["problems"]:
                print(f"      ! {p}")
    else:
        print(f"{sys.argv[1]} -> {sys.argv[2]} ({sys.argv[3] if len(sys.argv) > 3 else 'Any'}):")
        auction.preview(*sys.argv[1:4])

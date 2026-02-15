# -*- coding: utf-8 -*-
import argparse
import json
import pandas as pd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="norm.json")
    ap.add_argument("--s_cols", default="S1,S2,S3,S4,S5")
    ap.add_argument("--eps", type=float, default=1e-8)
    args = ap.parse_args()

    cols = [c.strip() for c in args.s_cols.split(",") if c.strip()]
    df = pd.read_csv(args.csv)
    x = df[cols].astype(float).to_numpy()
    mean = x.mean(axis=0).tolist()
    var = x.var(axis=0).tolist()
    with open(args.out, "w") as f:
        json.dump({"cols": cols, "mean": mean, "var": var, "eps": args.eps}, f)
    print("Wrote:", args.out)

if __name__ == "__main__":
    main()
